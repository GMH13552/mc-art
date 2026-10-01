"""The ore embedding band: the engine's default is for ITEMS, and says so.

The engine's `accent_base_gap` default (6..24) is calibrated on an item's accent --
one deposit's mean, iron_ore at +12.24. Ore specks are not like that: measured per
deposit over the eight vanilla ores, the gap runs -75.7 (redstone) to +106.6
(gold), and none of them falls inside the default band. A real project treated the
default as an ore standard and spent seven or eight rounds compressing a deposit
that was supposed to jump out.

So: an asset whose declared category is a deposit must declare its own band, taken
from the references it measured. The engine names the way out instead of pretending
its item band is a general rule.

    python scripts/check-ore-gap.py            # both cases
    python scripts/check-ore-gap.py --fault    # the reverse fixtures, isolated

`mc-art gap-from-refs <textures>` measures a band; the per-deposit range is the
one to declare, and the per-pixel range is the sharper boundary figure.
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

# A deposit: chromatic specks on a grey stone base. Pitched INSIDE the measured ore
# range (-75.7..+106.6 per deposit) rather than louder than gold, so the fixture
# tests the band and not the extreme.
SPECK = (216, 184, 96)
STONE = (127, 133, 144)
SPECK_PIXELS = [(3, 4), (4, 4), (5, 4), (4, 5), (9, 9), (10, 9), (10, 10), (2, 11), (3, 11)]


def _write_assets(root: Path) -> None:
    (root / "refs").mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (16, 16), (*STONE, 255))
    for x, y in SPECK_PIXELS:
        image.putpixel((x, y), (*SPECK, 255))
    # The reference is the thing a band should be measured FROM, so it carries the
    # same high-contrast relationship.
    image.save(root / "refs" / "gold_ore.png", "PNG")
    Image.new("RGBA", (16, 16), (*STONE, 255)).save(root / "refs" / "stone.png", "PNG")


def _plan(name: str, *, declare_band: bool = False, band: tuple[float, float] = (-90.0, 120.0)) -> dict:
    appearance = {
        "palette": {
            "rock_1": "#3A4049", "rock_2": "#474F5A", "rock_3": "#565F6C",
            "rock_4": "#6A7480", "rock_5": "#7F8A96",
            "speck_1": "#D8B860", "speck_2": "#C0A050", "speck_3": "#A08040",
        },
        "parts": {
            "face": {"colors": ["rock_1", "rock_2", "rock_3", "rock_4", "rock_5"],
                     "shade_axis": "none", "material": "stone"}
        },
        "transparent_background": False,
        "reference_sampling": "value",
        "part_reference_sampling": {"face": "value"},
        "part_reference_sources": {},
        "part_reference_composite": {},
        "motif_policy": "model_authored",
        "accent_colors": ["#D8B860", "#C0A050", "#A08040"],
        "accent_budget": 40,
        "accent_min_cluster": 2,
        "accent_cleanup": False,
        "accent_edge_max": 260,
        "band_maximum_isolated": 0.2,
        "band_maximum_step": 200,
        "pixel_map": {
            "legend": {"1": "#D8B860", "2": "#C0A050"},
            # Three DISTINCT deposits -- different sizes, shapes and gaps. The
            # structure gates are right to refuse three identical stamps, and this
            # fixture is about the embedding band, so it must not trip them.
            "rows": [
                "................",
                "................",
                "................",
                "..1.............",
                "..1.............",
                "................",
                "................",
                "................",
                ".........1......",
                "........111.....",
                ".........1......",
                "................",
                "....11..........",
                ".....11.........",
                "................",
                "................",
            ],
        },
    }
    if declare_band:
        appearance["accent_base_gap_min"] = band[0]
        appearance["accent_base_gap_max"] = band[1]
        appearance["threshold_waiver"] = (
            "an ore deposit's specks are measured from the ore family, not from the engine's item "
            "band: eight vanilla ores run -75.7..+106.6 per deposit"
        )
    return {
        "request": {
            "namespace": "examplepack", "name": name, "width": 16, "height": 16,
            "seed": 3, "shape_policy": "reference", "query": "%s ore block face" % name,
        },
        "descriptor": {
            "target": name, "semantic": "%s ore block face" % name,
            "visual_identity": ["16x16 opaque block face", "chromatic specks on grey stone"],
            "parts": [{"id": "face", "meaning": "the block's face"}],
            "shape_edit_mode": "appearance_only",
            "class": "ore_deposit",
            "layer": "shallow",
        },
        "geometry": {
            "width": 16, "height": 16, "background_transparent": False,
            "parts": [{"id": "face", "meaning": "the block's face"}],
            "primitives": [{
                "primitive": "rect", "part_id": "face", "params": {"x": 0, "y": 0, "w": 16, "h": 16},
            }],
            "uv_regions": [{
                "id": "face_all", "part_id": "face", "bbox": [0, 0, 16, 16], "face": "all",
                "required": True,
            }],
            "constraints": [], "connections": [],
        },
        "appearance": appearance,
        "references": [{
            "path": "../refs/gold_ore.png", "name": "gold_ore",
            "roles": ["shape", "material", "pixel_style"], "class": "ore_deposit",
            "notes": ["the deposit this band is measured from"],
        }],
    }


def run_case(root: Path, name: str, *, measured_band: bool = False, **kwargs) -> tuple[bool, list[str], str]:
    # Each case is self-contained: its own refs, plans and output, so the two
    # cases cannot influence each other through a shared reference root.
    _write_assets(root)
    if measured_band:
        # The intended workflow: measure the references, then declare what they
        # measured. Not a number chosen to make the gate pass.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "measure_accent_gap", ROOT / "scripts" / "measure-accent-gap.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        rows = [module.measure(path) for path in sorted((root / "refs").glob("*.png"))]
        lows, highs = [], []
        for row in rows:
            if "per_deposit" in row:
                lows.append(row["per_deposit"]["min"])
                highs.append(row["per_deposit"]["max"])
        if lows:
            # A family band, not a single reference's: the feedback's own example
            # declares -90..+120 for exactly this reason, because a family spans
            # several ores and the product's base is not the reference's base. The
            # margin is stated rather than hidden.
            band = (min(lows) - 40.0, max(highs) + 60.0)
            print("  measured from refs: %+.1f .. %+.1f -> declaring family band %+.1f .. %+.1f"
                  % (min(lows), max(highs), band[0], band[1]))
            kwargs["band"] = band
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / ("%s.plan.json" % name)).write_text(
        json.dumps(_plan(name, **kwargs), indent=2) + "\n", encoding="utf-8"
    )
    pipeline = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0)
    failure = pipeline.run(plan_from_file(plans / ("%s.plan.json" % name)), root / "out", package=False)
    interesting = [
        error for error in failure.validation.errors
        if "embedding band" in error or "accent_base_gap" in error or "not embedded" in error
    ]
    gap = failure.validation.metrics.get("style.accent_base_signed_gap")
    return failure.validation.passed, interesting, "signed gap %s" % gap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true")
    parser.add_argument("--keep", help="keep the generated tree")
    args = parser.parse_args()

    scratch = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="mc-art-ore-gap-"))
    try:
        _write_assets(scratch)

        print("CASE 1: an ore_deposit that did NOT declare a band (uses the item default)")
        passed, messages, note = run_case(scratch / "undeclared", "example_ore_undeclared")
        print("  validation=%s   %s" % ("PASS" if passed else "FAIL", note))
        for message in messages:
            print("    %s" % message)
        case1_red = not passed and bool(messages)
        print("  -> %s" % ("REFUSED with guidance" if case1_red else "DID NOT FIRE"))
        print()

        print("CASE 2: the same asset, band declared from the ore family (-90..+120)")
        passed2, messages2, note2 = run_case(
            scratch / "declared", "example_ore_declared", measured_band=True, declare_band=True
        )
        print("  validation=%s   %s" % ("PASS" if passed2 else "FAIL", note2))
        for message in messages2:
            print("    %s" % message)
        print("  -> %s" % ("accepted" if passed2 else "STILL REFUSED"))
        print()

        if args.fault:
            print("REVERSE FIXTURE 1: the undeclared ore must be refused")
            print("  %s" % ("PASS (went red as required)" if case1_red else "FAIL (did not go red)"))
            print()
            print("REVERSE FIXTURE 2: the declared ore must be accepted")
            print("  %s" % ("PASS (went green as required)" if passed2 else "FAIL (stayed red)"))
            print()
            ok = case1_red and passed2
            print("fault gates: %s" % ("both behave as required" if ok else "ONE DID NOT"))
            return 0 if ok else 1
        return 0 if (case1_red and passed2) else 1
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
