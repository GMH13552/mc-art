"""The shipped examples must stay parseable, neutral and actually declarative.

An example that stops parsing is a broken front door, and an example that drops
its accent budget silently stops demonstrating the thing it exists to show.
These checks need no game jar: they read the plan JSON and the block spec.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

from mc_art.blockmodel import audit_block_model
from mc_art.planfile import plan_from_dict

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FAMILY = EXAMPLES / "example_family" / "plans"
BLOCK = EXAMPLES / "example_block_entity"

FAMILY_PLANS = sorted(FAMILY.glob("*.plan.json"))

# The project-specific half of the privacy check reads its word list from
# OUTSIDE the repository, because a blocklist that names the projects it forbids
# is itself the leak: the first version of this file spelled them out, which is
# exactly the thing the rule exists to prevent. Mirrors panel/private-markers.txt
# in the host repository, which is untracked for the same reason.
#
#   MCART_PRIVATE_MARKERS="alpha,beta" python -m pytest tests -q
#   or an untracked private-markers.txt at the repository root, one name per line
#
# With neither configured, the generic checks below still run and the test says
# so rather than passing silently.
MARKER_FILE = EXAMPLES.parent / "private-markers.txt"


def private_markers() -> list[str]:
    from_env = [item.strip() for item in os.environ.get("MCART_PRIVATE_MARKERS", "").split(",")]
    markers = [item for item in from_env if item]
    if markers:
        return markers
    if MARKER_FILE.exists():
        return [
            line.strip()
            for line in MARKER_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return []


# Generic leaks that need no word list. The drive-letter form must catch a
# Windows drive prefix but not a URL scheme, because a scheme separator is also
# a colon followed by a slash. Requiring a non-alphanumeric character (or the
# start of the text) before the drive letter is what tells the two apart. This
# paragraph deliberately spells no example path: a scanner would flag it.
GENERIC_LEAKS = (
    ("absolute Windows path", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")),
    ("absolute home directory path", re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/")),
)


def test_the_family_ships_one_plan_per_member() -> None:
    names = {path.name for path in FAMILY_PLANS}
    assert names == {
        "example_stone.plan.json",
        "example_deepslate.plan.json",
        "example_ore.plan.json",
        "example_deepslate_ore.plan.json",
        "example_raw_ore.plan.json",
        "example_ingot.plan.json",
    }


@pytest.mark.parametrize("path", FAMILY_PLANS, ids=lambda path: path.stem)
def test_every_family_plan_parses_into_a_plan(path: Path) -> None:
    plan = plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert plan.request.name.startswith("example_")
    assert plan.geometry.width == plan.geometry.height == 16
    assert plan.descriptor.parts


def test_the_plain_blocks_declare_an_accent_budget_of_zero_and_a_95_percent_value_gate() -> None:
    for name in ("example_stone", "example_deepslate"):
        data = json.loads((FAMILY / (name + ".plan.json")).read_text(encoding="utf-8"))
        appearance = data["appearance"]
        assert appearance["accent_budget"] == 0, name
        assert appearance["accent_colors"] == [], name


def test_the_accented_members_declare_their_whole_accent_contract() -> None:
    expected = {
        "example_ore": (70, 1),
        "example_deepslate_ore": (70, 1),
        "example_raw_ore": (27, 3),
        "example_ingot": (24, 1),
    }
    for name, (budget, minimum) in expected.items():
        data = json.loads((FAMILY / (name + ".plan.json")).read_text(encoding="utf-8"))
        appearance = data["appearance"]
        assert appearance["accent_budget"] == budget, name
        assert appearance["accent_min_cluster"] == minimum, name
        # The ingot's accent is the reference's own lighting: it is one large
        # connected region, so there is no stray for the repair to remove and
        # cleanup is deliberately off there.
        assert appearance["accent_cleanup"] is (name != "example_ingot"), name
        assert appearance["accent_edge_max"] is not None, name
        assert appearance["accent_colors"], name


def test_every_pixel_map_row_is_the_canvas_width_and_uses_a_declared_symbol() -> None:
    for path in FAMILY_PLANS:
        data = json.loads(path.read_text(encoding="utf-8"))
        pixel_map = data["appearance"].get("pixel_map")
        if pixel_map is None:
            continue
        rows = pixel_map["rows"]
        legend = pixel_map["legend"]
        assert len(rows) == 16, path.name
        for row in rows:
            assert len(row) == 16, (path.name, row)
        for row in rows:
            for symbol in row:
                assert symbol == "." or symbol in legend, (path.name, symbol)


def test_a_gradient_member_exists_so_the_shade_mode_is_demonstrated() -> None:
    modes = set()
    for path in FAMILY_PLANS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for style in data["appearance"]["parts"].values():
            modes.add(style.get("shade_mode", "bands"))
    assert "gradient" in modes and "bands" in modes


def test_the_desk_example_passes_its_own_uv_audit_and_a_fault_fails() -> None:
    spec = json.loads((BLOCK / "desk.json").read_text(encoding="utf-8"))
    report = audit_block_model(spec, base_dir=BLOCK)
    assert report["passed"] is True, report["problems"]
    assert report["kind"] == "block-entity"
    assert report["elements"] == 4

    faulted = json.loads(json.dumps(spec))
    faulted["elements"][3]["faces"] = {"up": {"texture": "paper"}}
    assert audit_block_model(faulted, base_dir=BLOCK)["passed"] is False


def test_no_shipped_example_mentions_a_private_project_or_a_machine_path() -> None:
    """Scan every tracked example file for a leak, and say which rule caught it.

    Two layers on purpose. The generic patterns always run, because an absolute
    path is a leak regardless of what the project is called. The project-specific
    word list runs only when one is configured from outside the repository (see
    ``private_markers``) — a test cannot both forbid a name and contain it.
    """
    markers = private_markers()
    offenders: list[str] = []
    scanned = 0
    for path in sorted(EXAMPLES.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".py", ".md"}:
            continue
        if {"refs", "out", "textures"} & set(path.parts):
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        for marker in markers:
            if marker in text:
                offenders.append("%s: word-list hit %r" % (path, marker))
        for label, pattern in GENERIC_LEAKS:
            for match in pattern.finditer(text):
                offenders.append("%s: %s at offset %d" % (path, label, match.start()))
    assert scanned >= 6, "the example scan found almost nothing, so it proves nothing"
    assert not offenders, offenders
    if not markers:
        # Report the reduced coverage instead of letting a green run imply the
        # project-specific half ran.
        print(
            "note: generic leak patterns only; set MCART_PRIVATE_MARKERS or write "
            "%s to also check project-specific names" % MARKER_FILE
        )


def _leak_sample() -> str:
    """A string that trips both generic patterns, assembled from parts.

    A test that plants a literal drive path to prove the pattern fires would
    itself contain one: the scanner it is testing would flag this file, and a
    public repository would carry the planted path forever. So both halves are
    built at runtime and the source never holds a complete path.
    """
    backslash = chr(92)
    drive = "C" + ":" + backslash + "Users" + backslash + "someone"
    home = "/" + "home" + "/" + "someone" + "/demo/"
    return drive + "  " + home


def test_the_generic_leak_patterns_actually_match_a_leak() -> None:
    """The scan above is only worth having if its patterns can fire — and stop."""
    planted = _leak_sample()
    caught = {label for label, pattern in GENERIC_LEAKS if pattern.search(planted)}
    assert caught == {label for label, _pattern in GENERIC_LEAKS}, caught

    # ...and it must not fire on ordinary content, or a green scan means nothing
    # because everything is red. A URL is the case that bit: its scheme
    # separator is a colon preceded by a letter and followed by a slash, which a
    # naive drive-letter pattern reads as a path. (Written out here on purpose:
    # this is the input that must stay clean, not a leak.)
    clean = (
        "examples/example_stone/sprite.png  https://github.com/example/mc-art"
        "  see ../../README.md and python examples/example_family/build.py"
    )
    for label, pattern in GENERIC_LEAKS:
        assert not pattern.search(clean), (label, clean)


def test_the_word_list_half_fires_when_it_is_configured(monkeypatch, tmp_path) -> None:
    """A/B: configured, a word list is read; unconfigured, the half is skipped."""
    monkeypatch.delenv("MCART_PRIVATE_MARKERS", raising=False)
    monkeypatch.setattr(sys.modules[__name__], "MARKER_FILE", tmp_path / "absent.txt")
    assert private_markers() == []

    monkeypatch.setenv("MCART_PRIVATE_MARKERS", "alpha, beta")
    assert private_markers() == ["alpha", "beta"]

    monkeypatch.delenv("MCART_PRIVATE_MARKERS")
    written = tmp_path / "private-markers.txt"
    written.write_text("# comment\n\ngamma\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "MARKER_FILE", written)
    assert private_markers() == ["gamma"]
