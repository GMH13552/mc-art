"""The tiling seam gate: a pattern on the tile edge shows up as half a pattern.

A block texture is tiled. A speck on row 0 shows up cut in half where the next
block starts, which reads as a seam -- the user's own words were "why is there an
ore speck on the edge, if this joins stone it ruins it".

Measured over the eight vanilla ores, six keep at least one pixel of margin and
only emerald touches the border. The engine had no such check, while the skill's
"who decides what" table listed *tiling seams* among the things pixel statistics
cover -- a table promising a check the engine did not perform.

    python scripts/check-tiling.py            # both cases
    python scripts/check-tiling.py --fault    # the reverse fixtures
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from mc_art.pipeline import GenerationPipeline  # noqa: E402
from mc_art.planfile import plan_from_file  # noqa: E402

STONE = (127, 133, 144)
SPECK = (216, 184, 96)

# Three distinct deposits well inside the tile (margin 3+), and one that touches
# the left column -- the fault.
INSIDE_ROWS = [
    "................",
    "................",
    "................",
    "...1............",
    "................",
    "................",
    ".........1......",
    "........1.1.....",
    ".........1......",
    "................",
    "................",
    "................",
    "................",
    ".....11.........",
    "......1.........",
    "................",
]
EDGE_ROWS = [
    "................",
    "................",
    "................",
    "1...............",   # column 0: this is the seam
    "................",
    "................",
    ".........1......",
    "........1.1.....",
    ".........1......",
    "................",
    "................",
    "................",
    "................",
    ".....11.........",
    "......1.........",
    "................",
]


def _write(root: Path) -> None:
    (root / "refs").mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (16, 16), (*STONE, 255))
    image.save(root / "refs" / "stone_ore.png", "PNG")


def _plan(name: str, rows: list[str], margin: int | None) -> dict:
    appearance = {
        "palette": {
            "rock_1": "#3A4049", "rock_2": "#474F5A", "rock_3": "#565F6C",
            "rock_4": "#6A7480", "rock_5": "#7F8A96",
            "speck_1": "#D8B860", "speck_2": "#C0A050",
        },
        "parts": {"face": {"colors": ["rock_1", "rock_2", "rock_3", "rock_4", "rock_5"],
                           "shade_axis": "none", "material": "stone"}},
        "transparent_background": False,
        "reference_sampling": "value",
        "part_reference_sampling": {"face": "value"},
        "part_reference_sources": {},
        "part_reference_composite": {},
        "motif_policy": "model_authored",
        "accent_colors": ["#D8B860", "#C0A050"],
        "accent_budget": 40,
        "accent_min_cluster": 1,
        "accent_cleanup": False,
        "accent_edge_max": 260,
        "band_maximum_isolated": 0.2,
        "band_maximum_step": 200,
        "accent_motif_repeat_max": None,
        "accent_layout_min_size_cv": None,
        "accent_layout_min_spacing_cv": None,
        "accent_ramp_min_levels": None,
        "accent_ramp_min_monotone": None,
        "accent_ramp_max_dominant_share": None,
        "accent_base_gap_min": -75.7,
        "accent_base_gap_max": 106.6,
        "threshold_waiver": "ore-family band; limits widened for the fixture so only tiling is under test",
        "pixel_map": {"legend": {"1": "#D8B860"}, "rows": rows},
    }
    if margin is not None:
        appearance["tiling_min_margin"] = margin
    return {
        "request": {"namespace": "examplepack", "name": name, "width": 16, "height": 16,
                    "seed": 5, "shape_policy": "planned", "query": "%s ore block" % name},
        "descriptor": {
            "target": name, "semantic": "%s ore block face" % name,
            "visual_identity": ["16x16 opaque block face"],
            "parts": [{"id": "face", "meaning": "the block's face"}],
            "shape_edit_mode": "appearance_only",
            "class": "ore_deposit", "layer": "shallow",
        },
        "geometry": {
            "width": 16, "height": 16, "background_transparent": False,
            "parts": [{"id": "face", "meaning": "the block's face"}],
            "primitives": [{"primitive": "custom_mask", "part_id": "face",
                            "params": {"rows": ["X" * 16] * 16}}],
            "uv_regions": [{"id": "face_all", "part_id": "face", "bbox": [0, 0, 16, 16],
                            "face": "all", "required": True}],
            "constraints": [], "connections": [],
        },
        "appearance": appearance,
        "references": [{"path": "../refs/stone_ore.png", "name": "stone_ore",
                        "roles": ["shape", "material", "pixel_style"], "class": "ore_deposit",
                        "notes": ["the tile this pattern has to survive"]}],
    }


def run_case(root: Path, name: str, rows: list[str], margin: int | None) -> tuple[bool, list[str], dict]:
    _write(root)
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / ("%s.plan.json" % name)).write_text(
        json.dumps(_plan(name, rows, margin), indent=2) + "\n", encoding="utf-8"
    )
    pipeline = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0)
    failure = pipeline.run(plan_from_file(plans / ("%s.plan.json" % name)), root / "out", package=False)
    messages = [error for error in failure.validation.errors if "pattern pixels reach" in error]

    def metric(key: str):
        # A single stage reports `tiling_ok`; the aggregated result prefixes it
        # with the stage name. Accept either so the reader is not the bug.
        return failure.validation.metrics.get(key, failure.validation.metrics.get("style." + key))

    return failure.validation.passed, messages, {
        "tiling_ok": metric("tiling_ok"),
        "margin": metric("tiling_minimum_margin"),
        "border": metric("tiling_border_pixels"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true")
    args = parser.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="mc-art-tiling-"))
    try:
        print("CASE 1: a deposit on column 0, with tiling_min_margin declared as 1")
        passed1, messages1, m1 = run_case(scratch / "edge", "example_ore_on_the_seam",
                                          EDGE_ROWS, 1)
        print("  validation=%s  %s" % ("PASS" if passed1 else "FAIL", m1))
        for message in messages1:
            print("    %s" % message)
        case1_red = not passed1 and bool(messages1)
        print("  -> %s" % ("REFUSED" if case1_red else "DID NOT FIRE"))
        print()

        print("CASE 2: the same pattern moved inside the tile")
        passed2, messages2, m2 = run_case(scratch / "inside", "example_ore_inside_the_tile",
                                          INSIDE_ROWS, 1)
        print("  validation=%s  %s" % ("PASS" if passed2 else "FAIL", m2))
        for message in messages2:
            print("    %s" % message)
        print("  -> %s" % ("accepted" if passed2 else "STILL REFUSED"))
        print()

        if args.fault:
            print("REVERSE FIXTURE 1: a pattern on the tile edge must be refused")
            print("  %s" % ("PASS (went red as required)" if case1_red else "FAIL (did not go red)"))
            print()
            print("REVERSE FIXTURE 2: the same pattern inside the tile must be accepted")
            print("  %s" % ("PASS (went green as required)" if passed2 else "FAIL (stayed red)"))
            print()
            ok = case1_red and passed2
            print("fault gates: %s" % ("both behave as required" if ok else "ONE DID NOT"))
            return 0 if ok else 1
        return 0 if (case1_red and passed2) else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
