import json
import urllib.error
from pathlib import Path

import pytest

from deckpool import pull
from deckpool.convert import NameSet, convert

FIXTURES = Path(__file__).parent / "fixtures" / "mtgtop8"
CARDS = ["Mountain", "Plains", "Arid Mesa", "Marsh Flats", "Swords to Plowshares", "Plateau", "Wasteland", "Karakas",
         "Sunbaked Canyon", "Goblin Bombardment", "Elegant Parlor", "Sand Scout", "Static Prison", "Amped Raptor",
         "Ocelot Pride", "Ajani, Nacatl Pariah", "Guide of Souls", "Windswept Heath", "Lazotep Quarry",
         "Agate Instigator", "Voice of Victory", "Hexing Squelcher", "Surgical Extraction", "Wear", "Tear",
         "Null Rod", "Price of Progress", "Red Elemental Blast", "Deafening Silence", "Erode"]
ALL_NAMES = NameSet(CARDS)


def test_the_metagame_page_gives_period_and_archetypes_without_other_buckets():
    period, archetypes = pull.parse_metagame((FIXTURES / "format_LE.html").read_text(encoding="utf-8"))
    names = [a.name for a in archetypes]
    assert period == "Last 2 Weeks"
    assert len(names) == 58 and not any(n.startswith("Other") for n in names)
    assert "Death & Taxes" in names and names[0] == "Boros Aggro"
    top = pull.top_archetypes(archetypes)
    assert len(top) == 40 and top[0].name == "Hogaak" and top[0].share == 9.0


def test_same_name_rows_merge_into_one_archetype():
    page = ('METAGAME BREAKDOWN<div padding:3px;" class=S14>Last 2 Weeks</div>'
            '<a href=archetype?a=1&meta=34&f=LE>Jeskai Control</a></div><div><div x class=S14>3 %</div>'
            '<a href=archetype?a=2&meta=34&f=LE>Jeskai Control</a></div><div><div x class=S14>2 %</div>')
    _, archetypes = pull.parse_metagame(page)
    assert [(a.name, a.share, a.ids) for a in archetypes] == [("Jeskai Control", 5.0, [1, 2])]


def test_the_archetype_page_gives_decks_newest_first_with_their_labels():
    refs = pull.parse_archetype((FIXTURES / "archetype_1852.html").read_text(encoding="utf-8"))
    assert len(refs) == 20 and refs[0].deck_id == 908677 and refs[0].label == "Boros Aggro"
    assert refs[0].url == "https://mtgtop8.com/event?e=93260&d=908677&f=LE"
    assert refs[0].export_url == "https://mtgtop8.com/dec?d=908677"
    assert [r.played for r in refs] == sorted((r.played for r in refs), reverse=True)
    assert any(r.label == "Boros Energy" for r in refs)


def test_a_list_is_skipped_only_when_its_label_names_another_archetype():
    names = ["Hogaak", "Pox", "Storm", "Ruby Storm", "Show and Tell", "Death & Taxes"]
    assert pull.label_belongs("Ub Hogaak", "Hogaak", names)
    assert not pull.label_belongs("Bg Hogaak", "Pox", names)
    assert pull.label_belongs("Red Storm", "Ruby Storm", names)
    assert not pull.label_belongs("Ruby Storm", "Storm", names)
    assert pull.label_belongs("Sneak Show", "Show and Tell", names)
    assert pull.label_belongs("BW Death & Taxes", "Death & Taxes", names)


def test_an_export_converts_with_brackets_comments_split_cards_and_sb_lines():
    names = NameSet(["Mountain", "Wear", "Tear", "Null Rod", "Arid Mesa"])
    text = convert("// made with mtgtop8\n4 [ZEN] Arid Mesa\n20 [A] Mountain\nSB:  1 [DGM] Wear / Tear\n"
                   "SB:  2 [WL] Null Rod\n", names, "x")
    assert text == "[metadata]\nName=x\n[Main]\n4 Arid Mesa\n20 Mountain\n[Sideboard]\n1 Wear // Tear\n2 Null Rod\n"


def test_the_saved_export_is_a_playable_list_and_an_unknown_name_is_named():
    text = (FIXTURES / "dec_908677.txt").read_text(encoding="utf-8")
    assert pull.check_export(text, ALL_NAMES) == (None, None)
    reason, unresolved = pull.check_export(text, NameSet(CARDS[:-1]))
    assert reason == "unresolved names" and unresolved == "Erode"


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def fake_site(blocked: str | None = None):
    pages = {"format?": (FIXTURES / "format_LE.html").read_bytes(),
             "archetype?": (FIXTURES / "archetype_1852.html").read_bytes(),
             "/dec?": (FIXTURES / "dec_908677.txt").read_bytes()}
    seen = []

    def opener(request, timeout):
        url = request.full_url
        seen.append((url, request.get_header("User-agent")))
        if blocked and blocked in url:
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        if url.endswith("/robots.txt"):
            return FakeResponse((pull.EXPECTED_ROBOTS + "\n").encode())
        return FakeResponse(next(body for key, body in pages.items() if key in url))
    return opener, seen


def make_fetcher(tmp_path, opener, sleeps):
    clock = [0.0]
    return pull.Fetcher(tmp_path / "cache", opener=opener, clock=lambda: clock[0],
                        sleep=lambda s: (sleeps.append(s), clock.__setitem__(0, clock[0] + s)))


def test_the_pull_spaces_requests_names_itself_and_refuses_a_second_run(tmp_path):
    opener, seen = fake_site()
    sleeps = []
    fetcher = make_fetcher(tmp_path, opener, sleeps)
    root = tmp_path / "repo"
    record = pull.run_pull(root, tmp_path / "cache", ALL_NAMES, fetcher, progress=lambda *_: None)
    assert record["metagame_period"] == "Last 2 Weeks" and record["archetypes_on_page"] == 58
    # every page is the Boros Aggro fixture: other archetypes skip its Boros Aggro lists and keep the Boros Energy ones
    assert len(record["archetypes"]) == 40 and len(record["decks"]) == 120
    assert record["archetypes_short_of_three"] == [] and record["dropped"]
    assert sleeps and min(sleeps) >= 6.0 and len(sleeps) == len(seen) - 1
    assert {agent for _, agent in seen} == {"mtg-forge-ai-deckpool/1.0"}
    assert all(url.startswith("https://mtgtop8.com/") for url, _ in seen)
    assert (root / "decks" / "raw" / "hogaak-1.txt").is_file() and (root / "decks" / "forge" / "hogaak-1.dck").is_file()
    assert json.loads((root / "decks" / "pull.json").read_text())["requests"] == len(seen)
    with pytest.raises(pull.AlreadyPulled):
        pull.run_pull(root, tmp_path / "cache", ALL_NAMES, fetcher, progress=lambda *_: None)
    before = len(seen)
    pull.run_pull(root, tmp_path / "cache", ALL_NAMES, fetcher, again=True, progress=lambda *_: None)
    assert len(seen) == before  # the cache answered every page


def test_a_403_stops_the_pull_without_a_retry(tmp_path):
    opener, seen = fake_site(blocked="archetype?")
    with pytest.raises(pull.PullStopped, match="403"):
        pull.run_pull(tmp_path / "repo", tmp_path / "cache", ALL_NAMES, make_fetcher(tmp_path, opener, []),
                      progress=lambda *_: None)
    assert sum("archetype?" in url for url, _ in seen) == 1
    assert not (tmp_path / "repo" / "decks" / "pull.json").exists()


def test_a_changed_robots_txt_stops_the_pull(tmp_path):
    def opener(request, timeout):
        return FakeResponse(b"User-agent: *\nDisallow: /\n")
    with pytest.raises(pull.PullStopped, match="robots"):
        pull.run_pull(tmp_path / "repo", tmp_path / "cache", ALL_NAMES, make_fetcher(tmp_path, opener, []),
                      progress=lambda *_: None)
