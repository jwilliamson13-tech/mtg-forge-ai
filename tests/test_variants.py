"""`Variant:` lines: alternate printings of one card."""

from cardscript import parse_card


def test_a_flavor_name_variant_is_printing_metadata_and_leaves_no_trace(script):
    (face,) = parse_card(script("agent_venom")).faces
    assert face.warnings == []
    assert not any("Rhilex" in t for a in face.abilities for t in a.tokens)


def test_attraction_light_variants_are_dropped_like_lights(script):
    (face,) = parse_card(script("information_booth")).faces
    assert face.warnings == []
    assert [a.tokens[0] for a in face.abilities] == ["Effect=Visit"]


def test_a_variant_carrying_rules_lines_is_a_warning_per_line(script):
    (face,) = parse_card(script("everythingamajig")).faces
    assert face.abilities == []
    assert face.warnings == [
        "unparsed variant line: A",
        "unparsed variant line: SVar",
        "unparsed variant line: A",
        "unparsed variant line: A",
        "unparsed variant line: SVar",
    ]
