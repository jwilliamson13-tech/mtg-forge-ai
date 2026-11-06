"""The one-off MTGTop8 Legacy pull: top archetypes by metagame share, up to three lists each.

Fetches only from mtgtop8.com, one request at a time, at least six seconds apart (its robots.txt asks
for five). Every fetched page is saved to a cache folder before anything reads it, so a resumed pull
never fetches a page twice. Any HTTP error stops the pull; there is no retry. The pull refuses to run
again once `decks/pull.json` exists, unless told otherwise.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from deckpool import manifest as mf
from deckpool.convert import MIN_MAIN_CARDS, DeckError, NameSet, UnresolvedNames, convert, normalize, parse_decklist

BASE = "https://mtgtop8.com"
USER_AGENT = "mtg-forge-ai-deckpool/1.0"
MIN_GAP_S = 6.0
TOP_ARCHETYPES = 40
LISTS_PER_ARCHETYPE = 3
# The robots.txt Josh read on 2026-11-06. A different one means stop and ask.
EXPECTED_ROBOTS = "User-agent: *\nDisallow: /compare\nCrawl-delay: 5\n\nUser-agent: Googlebot\nDisallow: /compare"
RECORD_NAME = "pull.json"


class PullStopped(RuntimeError):
    """The site said no, or looked different from what was read. The pull ends here."""


class AlreadyPulled(RuntimeError):
    pass


@dataclass
class Archetype:
    name: str
    share: float
    ids: list[int]


@dataclass
class DeckRef:
    deck_id: int
    event_id: int
    label: str
    player: str
    played: date

    @property
    def url(self) -> str:
        return f"{BASE}/event?e={self.event_id}&d={self.deck_id}&f=LE"

    @property
    def export_url(self) -> str:
        return f"{BASE}/dec?d={self.deck_id}"


def _text(raw: str) -> str:
    return html.unescape(raw).strip()


_ARCHETYPE_ROW = re.compile(
    r"href=archetype\?a=(\d+)&meta=\d+&f=LE>([^<]*)</a></div>\s*<div>\s*<div[^>]*>([\d.]+) %</div>")


def parse_metagame(page: str) -> tuple[str, list[Archetype]]:
    """The period the shares cover, and every real archetype with its share, in page order.

    The page groups archetypes (Aggro, Control, Combo) and ends each group with an `Other - ...`
    bucket. Those buckets are dropped. Rows with the same name merge into one archetype.
    """
    start = page.find("METAGAME BREAKDOWN")
    period = re.search(r'padding:3px;" class=S14>([^<]+)</div>', page[start:]) if start >= 0 else None
    if period is None:
        raise PullStopped("the format page has no metagame breakdown")
    rows = _ARCHETYPE_ROW.findall(page)
    if not rows:
        raise PullStopped("the format page lists no archetypes")
    merged: dict[str, Archetype] = {}
    for arch_id, raw_name, share in rows:
        name = _text(raw_name)
        if name.lower().startswith("other"):
            continue
        entry = merged.setdefault(normalize(name), Archetype(name, 0.0, []))
        entry.share += float(share)
        entry.ids.append(int(arch_id))
    return _text(period.group(1)), list(merged.values())


def top_archetypes(archetypes: list[Archetype], count: int = TOP_ARCHETYPES) -> list[Archetype]:
    """Highest share first. Ties keep page order; the page rounds shares, so ties are common."""
    return sorted(archetypes, key=lambda a: -a.share)[:count]


_DECK_ROW = re.compile(
    r"<a href=/event\?e=(\d+)&d=(\d+)&f=LE>([^<]*)</a></td>\s*<td><a class=player [^>]*>([^<]*)</a></td>"
    r".*?<td>(\d\d)/(\d\d)/(\d\d)</td>", re.S)


def parse_archetype(page: str) -> list[DeckRef]:
    """The decks an archetype page lists, newest first."""
    decks = [DeckRef(int(d), int(e), _text(label), _text(player), date(2000 + int(y), int(m), int(day)))
             for e, d, label, player, day, m, y in _DECK_ROW.findall(page)]
    return sorted(decks, key=lambda d: d.played, reverse=True)


def label_belongs(label: str, archetype: str, all_names) -> bool:
    """False when a list's own label names a different archetype than the page it sits on.

    MTGTop8 files sub-types under an archetype page (`Sneak Show` under Show and Tell, `BW Death & Taxes`
    under Death & Taxes), so a label that differs from the page name is normal and kept. A list is skipped
    only when its label contains another archetype's whole name, and that name isn't part of this
    archetype's own name (`Bg Hogaak` on the Pox page; `Red Storm` on Ruby Storm is fine).
    """
    tokens, own = set(normalize(label).split()), set(normalize(archetype).split())
    rivals = [set(normalize(n).split()) for n in all_names if normalize(n) != normalize(archetype)]
    hits = [r for r in rivals if r <= tokens and not r <= own]
    if not hits:
        return True
    return own <= tokens and len(own) >= max(len(r) for r in hits)


def check_export(text: str, names: NameSet) -> tuple[str | None, str | None]:
    """(None, None) when the export is a playable list. Otherwise a short reason and the unresolved names."""
    try:
        deck = parse_decklist(text)
    except DeckError as e:
        return f"unreadable: {e}", None
    if deck.main_cards < MIN_MAIN_CARDS:
        return f"main deck has {deck.main_cards} cards", None
    try:
        convert(text, names, "check")
    except UnresolvedNames as e:
        return "unresolved names", "; ".join(e.names)
    return None, None


class Fetcher:
    """One polite client: cached, serial, spaced, and unwilling to retry."""

    def __init__(self, cache: Path, gap_s: float = MIN_GAP_S, opener=urllib.request.urlopen,
                 clock=time.monotonic, sleep=time.sleep):
        self.cache, self.gap_s, self._open = Path(cache), max(gap_s, MIN_GAP_S), opener
        self._clock, self._sleep, self._last = clock, sleep, None
        self.cache.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.cache / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".bin")

    def get(self, url: str) -> str:
        if not url.startswith(BASE + "/"):
            raise PullStopped(f"refusing to fetch outside mtgtop8.com: {url}")
        path = self._path(url)
        if not path.is_file():
            path.write_bytes(self._request(url))  # saved before anything parses it
        raw = path.read_bytes()
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")

    def _request(self, url: str) -> bytes:
        if self._last is not None:
            wait = self._last + self.gap_s - self._clock()
            if wait > 0:
                self._sleep(wait)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        started = time.time()
        try:
            with self._open(request, timeout=30) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as e:
            self._finish(url, started, e.code)
            raise PullStopped(f"HTTP {e.code} for {url}; stopping, not retrying") from e
        except urllib.error.URLError as e:
            self._finish(url, started, 0)
            raise PullStopped(f"request failed for {url}: {e.reason}") from e
        self._finish(url, started, status)
        if b"captcha" in body[:20000].lower():
            raise PullStopped(f"captcha or challenge page at {url}; stopping")
        return body

    def _finish(self, url: str, started: float, status: int) -> None:
        self._last = self._clock()
        with open(self.cache / "requests.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": started, "url": url, "status": status}) + "\n")


def request_stats(cache: Path) -> dict:
    log = Path(cache) / "requests.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
    if not rows:
        return {"requests": 0, "first_request": None, "last_request": None, "span_minutes": 0}
    first, last = rows[0]["t"], rows[-1]["t"]
    return {"requests": len(rows), "first_request": datetime.fromtimestamp(first).isoformat(timespec="seconds"),
            "last_request": datetime.fromtimestamp(last).isoformat(timespec="seconds"),
            "span_minutes": round((last - first) / 60, 1)}


def check_robots(fetcher: Fetcher) -> None:
    body = fetcher.get(BASE + "/robots.txt")
    if body.replace("\r\n", "\n").strip() != EXPECTED_ROBOTS:
        raise PullStopped("robots.txt is not the one read on 2026-11-06; stopping to ask")


def slug(name: str) -> str:
    return normalize(name).replace(" ", "-")


def run_pull(root: Path, cache: Path, names: NameSet, fetcher: Fetcher | None = None, again: bool = False,
             progress=print) -> dict:
    """Pull the pool: decks/raw/*.txt, decks/forge/*.dck and decks/pull.json. The manifest is a later step."""
    root = Path(root)
    record_path = root / "decks" / RECORD_NAME
    if record_path.is_file() and not again:
        raise AlreadyPulled(f"{record_path} exists: the pool was already pulled. Pass --again to pull once more.")
    fetcher = fetcher or Fetcher(cache)
    check_robots(fetcher)
    period, archetypes = parse_metagame(fetcher.get(f"{BASE}/format?f=LE"))
    chosen = top_archetypes(archetypes)
    all_names = [a.name for a in archetypes]
    progress(f"{len(archetypes)} archetypes on the page for {period!r}; taking {len(chosen)}")
    decks, dropped, short = [], [], []
    for rank, arch in enumerate(chosen, 1):
        kept = 0
        for arch_id in arch.ids:
            if kept >= LISTS_PER_ARCHETYPE:
                break
            refs = parse_archetype(fetcher.get(f"{BASE}/archetype?a={arch_id}&meta=34&f=LE"))
            for ref in refs:
                if kept >= LISTS_PER_ARCHETYPE:
                    break
                if not label_belongs(ref.label, arch.name, all_names):
                    dropped.append({"archetype": arch.name, "url": ref.url, "reason": f"labeled {ref.label!r}"})
                    continue
                text = fetcher.get(ref.export_url).replace("\r\n", "\n")
                reason, unresolved = check_export(text, names)
                if reason:
                    dropped.append({"archetype": arch.name, "url": ref.url,
                                    "reason": reason + (f": {unresolved}" if unresolved else "")})
                    continue
                kept += 1
                deck_id = f"{slug(arch.name)}-{kept}"
                mf.write_deck_files(root, deck_id, text, convert(text, names, deck_id))
                decks.append({"id": deck_id, "archetype": arch.name, "source": ref.url,
                              "player": ref.player, "date": ref.played.isoformat()})
        if kept < LISTS_PER_ARCHETYPE:
            short.append({"archetype": arch.name, "lists": kept})
        progress(f"{rank}/{len(chosen)} {arch.name}: {kept} lists")
    record = {"source": BASE, "format": "Legacy", "metagame_period": period,
              "shares_are_rounded_by_the_site": True, "archetypes_on_page": len(archetypes),
              "archetypes": [{"name": a.name, "share": a.share} for a in chosen],
              "decks": decks, "archetypes_short_of_three": short, "dropped": dropped,
              "pulled_on": date.today().isoformat(), **request_stats(cache)}
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return record
