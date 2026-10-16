"""Turn one Forge card script into a `Card`.

A script is a list of `Key:Value` lines. Ability lines (`A:`, `T:`, `S:`,
`R:`) hold `|`-separated segments of the form `Key$ Value`. `SVar:Name:Body`
lines are never abilities by themselves; a segment value naming one pulls its
body in (see `_add_value`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cardscript.dropped import (
    DROPPED_COST_FIELDS,
    DROPPED_KEYS,
    DROPPED_KEYWORD_PARAMS,
    FLAVOR_PREFIX,
    LAST,
)
from cardscript.model import OWN_LINE_STATS, REF, VAR, Ability, Card, Face, KeyedNumber, ManaCost


class ParseError(ValueError):
    """A line that cannot be parsed. Carries the line and a short cause."""

    def __init__(self, line: str, cause: str):
        super().__init__(f"{cause}: {line}")
        self.line = line
        self.cause = cause


# Ability line key -> {first segment's key -> ability kind}. `ST$` is an
# ability that doesn't use the stack (Circling Vultures' discard, the `PayUp`
# abilities of Nafs Asp and friends); it reads as `activated`.
LINE_KINDS = {
    "A": {"SP": "spell", "AB": "activated", "ST": "activated"},
    "T": {"Mode": "trigger"},
    "S": {"Mode": "static"},
    "R": {"Event": "replacement"},
}

# An SVar body that is itself an ability, by its first segment's key. `Mode$`
# bodies are triggers, except under a static reference key.
SVAR_KINDS = {
    "DB": "sub",
    "AB": "activated",
    "ST": "activated",
    "SP": "spell",
    "Event": "replacement",
    "Mode": "trigger",
}

# Keys whose value names ability SVars (one, or a list joined by `,` or ` & `).
# Only these resolve: plenty of values merely match an SVar name
# (`Destination$ Exile` on a card with `SVar:Exile`). Measured over Forge's
# cardsfolder; add a key to the right group when the sweep finds a new one.
_RUN_KEYS = frozenset(
    {
        # Run as part of this ability.
        "Execute", "SubAbility", "ReplaceWith", "Choices", "RepeatSubAbility",
        "TrueSubAbility", "FalseSubAbility", "WinSubAbility", "LoseSubAbility",
        "HeadsSubAbility", "TailsSubAbility", "VoteSubAbility", "BidSubAbility",
        "OtherwiseSubAbility", "ChooseSubAbility", "CantChooseSubAbility",
        "ChooseNumberSubAbility", "PreventionSubAbility", "VoteTiedAbility",
        "FallbackAbility", "RegenerationAbility", "ReturnAbility", "MatchedAbility",
        "UnmatchedAbility", "ChosenPile", "UnchosenPile", "NextRoom", "Highest",
        "Lowest", "NotLowest", "GuessWrong", "GuessCorrect", "Trigger",
        "ExtraTurnDelayedTriggerExecute", "ExtraPhaseDelayedTriggerExcute",
    }
)
_GRANT_KEYS = frozenset(
    {
        # Granted to something: an ability, trigger or replacement.
        "Ability", "Abilities", "AddAbility", "AddAbilities", "Triggers",
        "AddTrigger", "AddTriggers", "TriggersWhenSpent", "ExtraPhaseDelayedTrigger",
        "ReplacementEffects", "Replacements", "AddReplacementEffect",
        "GainTextAbilities",
    }
)
# Granted statics: under these keys a `Mode$` body is a static, not a trigger.
STATIC_REFERENCE_KEYS = frozenset(
    {"StaticAbilities", "staticAbilities", "AddStaticAbility", "AddStaticAbilities",
     "StaticEffect", "Statics"}
)
REFERENCE_KEYS = _RUN_KEYS | _GRANT_KEYS | STATIC_REFERENCE_KEYS
# Keys whose value grants, removes or tests keywords, each item written like a
# `K:` line's value (`AddKeyword$ Flying & Ward:1`). Items are joined by ` & `.
# `KWChoice$ Flying,Rampage:3` joins them by `,`, and so do a few plain lists
# (`Keywords$ Vigilance,Reach`); elsewhere a comma belongs to a parameter
# (`Protection:Spell.Instant,Spell.Sorcery`).
KEYWORD_KEYS = frozenset(
    {
        "KW", "AddKeyword", "AddKeywords", "Keywords", "AddKWs", "PumpKeywords",
        "AddHiddenKeyword", "HiddenKeywords", "RemoveKeyword", "RemoveKeywords",
        "CantHaveKeyword", "ValidKeyword", "AddsKeywords", "SharedKeywords", "KWChoice",
    }
)
KEYWORD_CHOICE = "KWChoice"
_KEYWORD_LIST = re.compile(r"\s&\s")
_KEYWORD_COMMA = re.compile(r",(?!\s)")  # `, ` with a space is English

# Keywords whose parameters can name ability SVars (`K:Chapter:3:DBA,DBB,DBC`).
KEYWORD_REFERENCES = frozenset(
    {"Chapter", "ETBReplacement", "MayEffectFromOpeningHand", "MayEffectFromOpeningDeck",
     "Backup", "Visit", "Haunt", "Dungeon", "Prize"}
)
# Keywords whose effect lives in an SVar with a fixed name that no line names:
# Forge looks it up when it builds the keyword.
KEYWORD_SVARS = {"Gift": "GiftAbility", "Tribute": "TrigNotTribute"}
_REFERENCE_LIST = re.compile(r",|\s&\s")

# A die roll's outcomes: `ResultSubAbilities$ 1-14:DBA,15-20:DBB,Else:DBC`.
RESULT_KEY = "ResultSubAbilities"
_RESULT_RANGE = re.compile(r"(\d+)(?:-(\d+))?")

# How many SVar references deep a body is inlined. Past this, `Key=<ref>`.
INLINE_DEPTH = 1

# Values that are runtime quantities even with no SVar behind them.
BARE_VARS = frozenset({"X", "Y", "Z"})

# An SVar body computed at runtime: `Count$YourLifeTotal`, `SVar$Z1/Minus.Z2`,
# `TriggerCount$DamageAmount`. A word, `$`, then no space.
_RUNTIME_BODY = re.compile(r"\w+\$\S")

# Lines that split a script into faces: `ALTERNATE` (split, flip, transform,
# modal, adventure, ...) and `SPECIALIZE:<COLOR>` (Specialize's extra faces).
ALTERNATE = "ALTERNATE"
SPECIALIZE = "SPECIALIZE:"

COMMENT = "#"

# An ability's first token is `Effect=<API, mode, event or keyword name>`.
EFFECT = "Effect"

# `Variant:<id>:<Key>:...` lines (alternate printings) and `CopyFaceFrom:<face>`.
VARIANT = "Variant"
COPY_FACE = "CopyFaceFrom"

# `K:Protection from red` is read as `K:Protection:red`.
PROTECTION_FROM = "Protection from "

# A filter value splits on these, one `key=piece` token per piece: `.`, `+`
# and `,` inside a filter, and ` & ` between the items of a list
# (`AddTypes$ Legendary & Creature`).
_FILTER_SEPARATORS = re.compile(r"[.+,]|\s&\s")

COLORS = frozenset("WUBRG")

_INT = re.compile(r"[+-]?\d+")

# Cost grammar: space-separated atoms. An atom is an integer (generic mana), a
# mana symbol, a tap/untap flag, or a part like `ExileFromGrave<2/Card>`.
# A `<...>` part may hold spaces in its description field, hence the regex.
_COST_ATOM = re.compile(r"[^\s<]*<[^>]*>|\S+")
_COST_PART = re.compile(r"(\w+)<([^>]*)>")
# A mana symbol has a letter: `R`, `2W`, `RWP`, `X`. A bare integer is generic
# mana, a number.
_MANA_SYMBOL = re.compile(r"(?=.*[WUBRGCSPX])[WUBRGCSPX0-9/]+|XMin\d+")
# Tap and untap, and `CardManaCost` (the card's own mana cost, in a keyword
# granted to other cards: `KW$ Escape:CardManaCost ExileFromGrave<3/...>`).
COST_FLAGS = frozenset({"T", "Q", "CardManaCost"})


def parse_card(text: str) -> Card:
    """Parse a Forge card script's text into a plain-data `Card`.

    Raises `ParseError` for an ability line it can't read. A line key it
    doesn't know is a warning on the face instead.
    """
    return Card(faces=[_face(block) for block in _face_blocks(text.splitlines())])


def _face_blocks(lines: list[str]) -> list[list[str]]:
    """The script's lines, one block per face, in file order."""
    blocks: list[list[str]] = [[]]
    for raw in lines:
        line = raw.strip()
        if line == ALTERNATE or line.startswith(SPECIALIZE):
            blocks.append([])
        else:
            blocks[-1].append(raw)
    return blocks


@dataclass(frozen=True)
class _Scope:
    """What a value can see: the face's SVars, and how deep inlining has gone."""

    svars: dict[str, tuple[str, str]]  # name -> (script line, body)
    depth: int = 0

    def deeper(self) -> _Scope:
        return _Scope(self.svars, self.depth + 1)

    def is_ability(self, name: str) -> bool:
        """`name` is an SVar here whose body is an ability (`DB$ ...`, `Mode$ ...`)."""
        if name not in self.svars:
            return False
        return _svar_api(self.svars[name][1]) in SVAR_KINDS

    def is_var(self, value: str) -> bool:
        """`X`/`Y`/`Z`; an inline expression (`TriggerCount$Amount`); or the name of
        an SVar whose body is one (`SVar:X:Count$YourLifeTotal`).

        Unlike inlining, this holds under any key. Over Forge's cardsfolder every
        key naming a runtime SVar is a quantity (`NumDmg`, `CheckSVar`,
        `TargetMax`, ...), so there is no false hit to guard against.
        """
        if value in BARE_VARS or _RUNTIME_BODY.match(value):
            return True
        svar = self.svars.get(value)
        return svar is not None and bool(_RUNTIME_BODY.match(svar[1]))

    def inline(self, ability: Ability, key: str, name: str) -> Ability | None:
        """Append SVar `name`'s body to `ability` as a sub-ability and return it,
        or append `key=<ref>` and return None once past `INLINE_DEPTH`."""
        if self.depth >= INLINE_DEPTH:
            ability.tokens.append(f"{key}={REF}")
            return None
        line, body = self.svars[name]
        kinds = dict(SVAR_KINDS)
        if key in STATIC_REFERENCE_KEYS:
            kinds["Mode"] = "static"
        sub = _ability(self.deeper(), line, body, kinds)
        ability.subabilities.append(sub)
        return sub


def _svars(lines: list[str]) -> dict[str, tuple[str, str]]:
    """`SVar:Name:Body` lines of one face, minus dropped hint SVars. A
    redefined name keeps the last body."""
    svars = {}
    for raw in lines:
        line = raw.strip()
        if line.startswith("SVar:"):
            name, _, body = line[len("SVar:"):].partition(":")
            name = name.strip()
            if name not in DROPPED_KEYS:
                svars[name] = (line, body.strip())
    return svars


def _face(lines: list[str]) -> Face:
    """One face from its script lines. SVars are collected first, since a
    script often uses an SVar above the line that defines it."""
    face = Face()
    scope = _Scope(_svars(lines))
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(COMMENT):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            face.warnings.append(f"unrecognized line: {line}")
            continue
        value = value.strip()
        if key == "Name":
            face.name = value
        elif key == "ManaCost":
            face.mana_cost = _mana_cost(value)
        elif key == "Types":
            face.types = value.split()
        elif key == "Colors":
            face.colors = [c.strip() for c in value.split(",") if c.strip()]
        elif key == "PT":
            face.power, face.toughness = _power_toughness(value)
        elif key.lower() in OWN_LINE_STATS:  # `Loyalty:`, `Defense:`
            setattr(face, key.lower(), _printed_stat(value))
        elif key in LINE_KINDS:
            face.abilities.append(_ability(scope, line, value, LINE_KINDS[key]))
        elif key == "K":
            face.abilities.append(_keyword(scope, line, value))
        elif key == "SVar":
            continue  # Pulled in by the values that name it.
        elif key in DROPPED_KEYS:
            continue
        elif key == VARIANT:
            _variant(face, value)
        elif key == COPY_FACE:
            # Forge copies the named face from another script. parse_card sees
            # one script, so the face stays empty; the warning keeps the name.
            face.warnings.append(f"copied face not resolved: {value}")
        else:
            face.warnings.append(f"unknown key: {key}")
    return face



def _variant(face: Face, value: str) -> None:
    """`Variant:<id>:<Key>:...`: one line of an alternate printing.

    A line whose inner key is on the drop list (`FlavorName`, `Lights`,
    `Oracle`) is printing metadata and leaves no trace. Anything else (`A`,
    `SVar`, `PT`: the three Unstable cards whose printings differ in rules) is
    a warning, not parsed: one printing can't speak for the others.
    """
    _, _, rest = value.partition(":")
    inner = rest.partition(":")[0].strip()
    if inner not in DROPPED_KEYS:
        face.warnings.append(f"unparsed variant line: {inner}")


def _mana_cost(value: str) -> ManaCost:
    cost = ManaCost()
    if value == "no cost":
        return cost
    for symbol in value.split():
        if _INT.fullmatch(symbol):
            cost.generic += int(symbol)
        elif symbol in COLORS:
            cost.pips[symbol] = cost.pips.get(symbol, 0) + 1
        else:
            cost.special.append(symbol)
    return cost


def _power_toughness(value: str) -> tuple[int | str, int | str]:
    power, _, toughness = value.partition("/")
    return _printed_stat(power), _printed_stat(toughness)


def _printed_stat(value: str) -> int | str:
    """An integer, or `<var>` for anything computed at runtime (`*`, `1+*`, `X`)."""
    value = value.strip()
    return int(value) if _INT.fullmatch(value) else VAR


def _ability(scope: _Scope, line: str, value: str, kinds: dict[str, str]) -> Ability:
    """`First$ Effect | Key$ Value | ...`. `kinds` maps the first key to the kind."""
    segments = [_segment(line, s) for s in value.split("|")]
    api, effect = segments[0]
    if api not in kinds:
        raise ParseError(line, f"unknown ability API {api!r}")
    ability = Ability(kind=kinds[api], tokens=[f"{EFFECT}={effect}"])
    for key, val in segments[1:]:
        _add_segment(scope, ability, key, val)
    return ability



def _keyword(scope: _Scope, line: str, value: str) -> Ability:
    """`K:Name[:param[:param...]]` -> a keyword ability, `Effect=Name` plus
    its parameters (see `_add_keyword`). Gift and Tribute also inline the SVar
    Forge fetches for them by fixed name."""
    ability = Ability(kind="keyword")
    name = _add_keyword(scope, line, ability, EFFECT, value)
    implied = KEYWORD_SVARS.get(name)
    if implied and scope.is_ability(implied):
        scope.inline(ability, name, implied)
    return ability


def _add_keyword(scope: _Scope, line: str, ability: Ability, key: str, text: str) -> str:
    """One keyword, `Name[:param...]`: token `key=Name`, then its parameters.
    Returns the name.

    On a `K:` line `key` is `Effect` and the first parameter is keyed by the
    keyword name, later ones by `Name.<position>`, so Suspend's 5 time counters
    and its {G} never share a key. A keyword granted by a segment
    (`AddKeyword$ Ward:1`) prefixes those keys with the segment key:
    `AddKeyword.Ward`. `Protection from red` is read as `Protection:red`.
    """
    name, *params = text.split(":")
    name = name.strip()
    if not name:
        raise ParseError(line, "empty keyword")
    protected = None
    if name.startswith(PROTECTION_FROM):
        name, protected = "Protection", name[len(PROTECTION_FROM):]
    ability.tokens.append(f"{key}={name}")
    base = name if key == EFFECT else f"{key}.{name}"
    if protected is not None:
        ability.tokens.append(f"{base}={protected}")
    for position, param in enumerate(params, 1):
        if not _dropped_keyword_param(name, position, len(params), param):
            param_key = base if position == 1 else f"{base}.{position}"
            _add_keyword_param(scope, line, ability, param_key, param.strip())
    return name


def _dropped_keyword_param(keyword: str, position: int, count: int, param: str) -> bool:
    """English by position (`DROPPED_KEYWORD_PARAMS`) or a flavor word. A
    parameter with `Key$ Value` segments is never dropped."""
    if "$" in param:
        return False
    positions = DROPPED_KEYWORD_PARAMS.get(keyword, ())
    return (position in positions or (position == count and LAST in positions)
            or param.strip().startswith(FLAVOR_PREFIX))


def _add_keyword_param(scope: _Scope, line: str, ability: Ability, key: str, param: str) -> None:
    if not param:
        return
    if "$" in param:
        for segment in param.split("|"):
            _add_segment(scope, ability, *_segment(line, segment))
    elif _is_cost(param):
        _add_cost(scope, ability, key, param)
    else:
        _add_value(scope, ability, key, param)


def _is_cost(value: str) -> bool:
    """A multi-atom mana/cost string (`1 U`, `PayLife<3>`), not a lone value."""
    atoms = _COST_ATOM.findall(value)
    if len(atoms) == 1 and "<" not in value:
        return False
    return all(
        _COST_PART.fullmatch(a) or _INT.fullmatch(a) or a in COST_FLAGS or _MANA_SYMBOL.fullmatch(a)
        for a in atoms
    )


def _add_segment(scope: _Scope, ability: Ability, key: str, value: str) -> None:
    """One `Key$ Value` segment: dropped, parsed as a cost, or a plain value."""
    if key in DROPPED_KEYS:
        return
    if key.endswith("Cost"):
        _add_cost(scope, ability, key, value)
    else:
        _add_value(scope, ability, key, value)


def _add_value(
    scope: _Scope, ability: Ability, key: str, value: str, number_key: str | None = None
) -> None:
    """The one place a value is classified. First rule that fits:

    1. An integer: a number under `number_key`, which defaults to `key`.
    2. Under a reference key, a name or list of names of SVars whose bodies
       are abilities: each body inlined as a sub-ability, or `key=<ref>` once
       past `INLINE_DEPTH`.
    3. A runtime quantity, with an optional leading sign: `key=<var>`, then
       `key.sign=-` (or `+`) when signed. No number. See `_Scope.is_var`.
    4. Under a keyword key, a list of keywords: each read like a `K:` line's
       value, under `key` (see `_add_keyword`).
    5. Anything else: a filter, one `key=piece` token per piece. See
       `_add_tokens`.
    """
    if _INT.fullmatch(value):
        ability.numbers.append(KeyedNumber(number_key or key, int(value)))
        return
    if key == RESULT_KEY and _add_results(scope, ability, key, value):
        return
    if _is_reference_key(key):
        names = _REFERENCE_LIST.split(value)
        if all(scope.is_ability(name) for name in names):
            for name in names:
                scope.inline(ability, key, name)
            return
    sign, unsigned = (value[0], value[1:]) if value[:1] in ("+", "-") else ("", value)
    if scope.is_var(unsigned):
        ability.tokens.append(f"{key}={VAR}")
        if sign:
            ability.tokens.append(f"{key}.sign={sign}")
        return
    if key in KEYWORD_KEYS:
        for item in _keyword_items(key, value):
            _add_keyword(scope, item, ability, key, item)
        return
    _add_tokens(ability, key, value)


def _keyword_items(key: str, value: str) -> list[str]:
    """The keywords in a keyword key's value, in order. See `KEYWORD_KEYS`."""
    items = []
    for item in _KEYWORD_LIST.split(value):
        if key == KEYWORD_CHOICE or ":" not in item:
            items += _KEYWORD_COMMA.split(item)
        else:
            items.append(item)
    return [item.strip() for item in items if item.strip()]


def _add_tokens(ability: Ability, key: str, value: str) -> None:
    """Split on `.`, `+`, `,` and ` & `: `Creature.OppCtrl+tapped` gives
    `key=Creature`, `key=OppCtrl`, `key=tapped`.

    Pieces keep `!`, fused comparisons and inner spaces (`!token`, `cmcLE4`,
    `namedFestering Newt`). Empty pieces are skipped. A value with no
    separator is one token.
    """
    pieces = [p.strip() for p in _FILTER_SEPARATORS.split(value)]
    pieces = [p for p in pieces if p] or [value]
    ability.tokens.extend(f"{key}={piece}" for piece in pieces)


def _is_reference_key(key: str) -> bool:
    """A segment key in `REFERENCE_KEYS`, or a parameter of a keyword in
    `KEYWORD_REFERENCES` (keyed `Chapter` or `Chapter.2`)."""
    return key in REFERENCE_KEYS or key.partition(".")[0] in KEYWORD_REFERENCES


def _svar_api(body: str) -> str:
    """The first segment's key of an ability-shaped body (`DB$ Draw`), else ""."""
    first = body.split("|", 1)[0]
    key, sep, value = first.partition("$")
    return key.strip() if sep and value[:1].isspace() else ""


def _add_results(scope: _Scope, ability: Ability, key: str, value: str) -> bool:
    """`ResultSubAbilities$ 1-14:DBA,15-20:DBB` (a die roll's outcomes).

    Each `result:Name` inlines like any reference. A range `lo-hi` (or a single
    `n`) goes on that sub-ability as numbers `key.min` and `key.max`; a word
    such as `Else` as the token `key=Else`. False, and nothing added, unless
    every item names an ability SVar.
    """
    items = [item.partition(":") for item in value.split(",")]
    if not all(sep and scope.is_ability(name.strip()) for _, sep, name in items):
        return False
    for result, _, name in items:
        sub = scope.inline(ability, key, name.strip())
        if sub is None:
            continue
        span = _RESULT_RANGE.fullmatch(result.strip())
        if span:
            low, high = int(span[1]), int(span[2] or span[1])
            sub.numbers[:0] = [KeyedNumber(f"{key}.min", low), KeyedNumber(f"{key}.max", high)]
        else:
            sub.tokens.insert(1, f"{key}={result.strip()}")
    return True


def _add_cost(scope: _Scope, ability: Ability, key: str, value: str) -> None:
    """A cost: `R T ExileFromGrave<2/Card>` under key `Cost`.

    Mana symbols and T/Q are `key=symbol` tokens. A part
    `Name<amount/type/description>` is a `key=Name` token plus its fields (see
    `_add_cost_part`). Any other atom, generic mana included, is a plain value
    under `key`: an integer is a number, `Y` is `<var>`.
    """
    for atom in _COST_ATOM.findall(value):
        part = _COST_PART.fullmatch(atom)
        if part:
            _add_cost_part(scope, ability, key, part[1], part[2].split("/"))
        elif atom in COST_FLAGS or _MANA_SYMBOL.fullmatch(atom):
            ability.tokens.append(f"{key}={atom}")
        else:
            _add_value(scope, ability, key, atom)


def _add_cost_part(
    scope: _Scope, ability: Ability, key: str, name: str, fields: list[str]
) -> None:
    """`Name<amount/type/...>` under `key`: token `key=Name`, then each field as
    a value under `key.Name`. An integer past the amount is keyed by its
    position (`key.Name.2`) so it never shares a key with the amount.
    Description fields (`DROPPED_COST_FIELDS`) are dropped."""
    sub = f"{key}.{name}"
    ability.tokens.append(f"{key}={name}")
    dropped = DROPPED_COST_FIELDS.get(name, ())
    for position, field_ in enumerate(fields, 1):
        if not field_ or position in dropped:
            continue
        number_key = sub if position == 1 else f"{sub}.{position}"
        _add_value(scope, ability, sub, field_, number_key=number_key)


def _segment(line: str, segment: str) -> tuple[str, str]:
    key, sep, value = segment.partition("$")
    key, value = key.strip(), value.strip()
    if not sep or not key:
        raise ParseError(line, "segment without Key$")
    return key, value
