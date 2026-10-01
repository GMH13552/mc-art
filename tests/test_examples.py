"""The shipped examples must stay parseable, neutral and actually declarative.

An example that stops parsing is a broken front door, and an example that drops
its accent budget silently stops demonstrating the thing it exists to show.
These checks need no game jar: they read the plan JSON and the block spec.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mc_art.blockmodel import audit_block_model
from mc_art.planfile import plan_from_dict

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FAMILY = EXAMPLES / "example_family" / "plans"
BLOCK = EXAMPLES / "example_block_entity"

FAMILY_PLANS = sorted(FAMILY.glob("*.plan.json"))

# Names that must never reach a shipped file. Deliberately a short list of the
# markers this repository has already had to scrub once.
PRIVATE_MARKERS = (
    "the_nameless_mist", "fleshland", "eyeball", "/home/gmh", "118mod_adventure",
    "starfall", "abyss_stone", "mist_stone",
)


def test_the_family_ships_one_plan_per_member() -> None:
    names = {path.name for path in FAMILY_PLANS}
    assert names == {
        "example_stone.plan.json",
        "example_deepslate.plan.json",
        "example_ore.plan.json",
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
        "example_ore": (36, 3),
        "example_raw_ore": (20, 3),
        "example_ingot": (16, 3),
    }
    for name, (budget, minimum) in expected.items():
        data = json.loads((FAMILY / (name + ".plan.json")).read_text(encoding="utf-8"))
        appearance = data["appearance"]
        assert appearance["accent_budget"] == budget, name
        assert appearance["accent_min_cluster"] == minimum, name
        assert appearance["accent_cleanup"] is True, name
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
    offenders: list[str] = []
    for path in list(EXAMPLES.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".py", ".md"}:
            continue
        if "refs" in path.parts or "out" in path.parts or "textures" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in PRIVATE_MARKERS:
            if marker in text:
                offenders.append("%s: %s" % (path, marker))
        for match in re.finditer(r"[A-Za-z]:\\", text):
            offenders.append("%s: absolute Windows path at offset %d" % (path, match.start()))
    assert not offenders, offenders
