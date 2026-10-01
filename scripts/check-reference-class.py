"""Reference CLASS and NOTE gates, with the four plans that motivated them.

A real project shipped these four plans. The names and the contradictions are the
real ones; the texture files are generated here as flat swatches, because the
originals were vanilla art and this repository does not redistribute it.

    python scripts/check-reference-class.py            # the four plans
    python scripts/check-reference-class.py --fault    # the three fixtures, isolated

What went wrong, and which gate now catches it:

  example_mist_stone    a SHALLOW-declared stone that attached deepslate.png, with a
                        note saying the shallow variant "must NOT be chosen"
                        -> class gate: declared shallow, attached deep
  example_shallow_stone attached stone.png (shallow, correct) but carries the note
                        copied from example_deep_stone, which forbids the shallow stone
                        -> note gate: the note forbids the reference it sits on
  example_starfall_ore  note claims "the same reference example_shallow_stone uses", but
                        example_shallow_stone uses stone and this plan uses deepslate
                        -> note gate: the claim is checked against that plan
  example_deep_stone    attached deepslate.png, the note matches -- correct
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

# The note that was copied verbatim between plans -- the reason a wrong decision
# propagated into a document.
SHALLOW_IS_FORBIDDEN = (
    "same layer, same material role; a shallow-layer stone reference is the fault "
    "that already shipped once and must not be chosen"
)
DEEP_IS_REQUIRED = (
    "deep-layer stone. The shallow variant (minecraft:block/stone) is available in "
    "the same reference root and must NOT be chosen"
)

SWATCHES = {
    "stone": (127, 127, 127),
    "deepslate": (77, 77, 80),
    "iron_ore": (168, 139, 113),
}


def _write_refs(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, colour in SWATCHES.items():
        image = Image.new("RGBA", (16, 16), (*colour, 255))
        image.save(directory / ("%s.png" % name), "PNG")


def _plan(name: str, declared_class: str, reference: str, note: str,
          extra_reference: str | None = None) -> dict:
    references = [
        {
            "path": "../refs/%s.png" % reference,
            "name": reference,
            "roles": ["shape", "material", "pixel_style"],
            "class": ("shallow_stone" if reference == "stone" else
                      "deep_stone" if reference == "deepslate" else "ore_deposit"),
            "notes": [note],
        }
    ]
    if extra_reference:
        references.append({
            "path": "../refs/%s.png" % extra_reference,
            "name": extra_reference,
            "roles": ["material", "pixel_style"],
            "class": "ore_deposit",
            "notes": ["the deposits come from this reference's own pixels"],
        })
    return {
        "request": {
            "namespace": "examplepack", "name": name, "width": 16, "height": 16,
            "seed": 7, "shape_policy": "reference",
            "query": "%s block face" % name,
        },
        "descriptor": {
            "target": name, "semantic": "%s block face" % name,
            "visual_identity": ["16x16 opaque block face"],
            "parts": [{"id": "face", "meaning": "the block's face"}],
            "shape_edit_mode": "appearance_only",
            "class": declared_class,
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
        "appearance": {
            "palette": {
                "rock_1": "#3A4049", "rock_2": "#474F5A", "rock_3": "#565F6C",
                "rock_4": "#6A7480", "rock_5": "#7F8A96",
            },
            "parts": {"face": {"colors": ["rock_1", "rock_2", "rock_3", "rock_4", "rock_5"],
                               "shade_axis": "none", "material": "stone"}},
            "transparent_background": False,
            "reference_sampling": "pattern",
            "part_reference_sampling": {"face": "pattern"},
            "part_reference_sources": {},
            "part_reference_composite": {},
            "motif_policy": "model_authored",
            "accent_budget": 0,
            "band_maximum_isolated": 0.1,
            "band_maximum_step": 30,
        },
        "references": references,
    }


def build_fixture_tree(root: Path) -> Path:
    """The four plans, as the real project had them (shape reproduced exactly)."""
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    _write_refs(root / "refs")
    for name, declared, reference, note, extra in (
        # example_shallow_stone: shallow asset, shallow reference -- correct choice, but the
        # note it carries was copied from example_deep_stone and forbids that choice.
        ("example_shallow_stone", "shallow_stone", "stone", SHALLOW_IS_FORBIDDEN, None),
        # example_deep_stone: the note belongs here, and it matches.
        ("example_deep_stone", "deep_stone", "deepslate", SHALLOW_IS_FORBIDDEN, None),
        # example_mist_stone: a SHALLOW asset that took the DEEP reference, and wrote
        # "the shallow variant must NOT be chosen" into the plan.
        ("example_mist_stone", "shallow_stone", "deepslate", DEEP_IS_REQUIRED, None),
        # example_starfall_ore: deepslate base plus iron_ore deposits (as the real plan
        # had), claiming to use the same reference as example_shallow_stone. It does not.
        (
            "example_starfall_ore", "ore_deposit", "deepslate",
            "This is the same reference example_shallow_stone uses, so the ore's rock is the family's rock",
            "iron_ore",
        ),
    ):
        (plans / ("%s.plan.json" % name)).write_text(
            json.dumps(_plan(name, declared, reference, note, extra), indent=2) + "\n",
            encoding="utf-8",
        )
    return plans


def run(plans: Path, out: Path, label: str) -> int:
    pipeline = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0)
    print("== %s ==" % label)
    problems = 0
    for plan_path in sorted(plans.glob("*.plan.json")):
        plan_name = plan_path.name[: -len(".plan.json")]
        failure = pipeline.run(plan_from_file(plan_path), out / plan_name, package=False)
        errors = list(failure.validation.errors)
        interesting = [
            error for error in errors
            if error.startswith(("[reference_class]", "[reference_notes]", "[reference_pool]"))
        ]
        status = "PASS" if failure.validation.passed else "FAIL"
        print("  %-20s %s  (%d gate message(s))" % (plan_name, status, len(interesting)))
        for message in interesting:
            print("      %s" % message)
        problems += 0 if failure.validation.passed else 1
    print()
    print("  %d of 4 plans are refused" % problems)
    return problems


def _fixture(root: Path, name: str, declared: str, reference: str, note: str) -> int:
    plans = root / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / ("%s.plan.json" % name)).write_text(
        json.dumps(_plan(name, declared, reference, note), indent=2) + "\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true", help="isolate the three fixtures")
    parser.add_argument("--keep", help="keep the generated tree at this path")
    args = parser.parse_args()

    scratch = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="mc-art-refclass-"))
    try:
        if args.fault:
            print("REVERSE FIXTURE A: a shallow asset that attaches the deep reference")
            root = scratch / "a"
            _write_refs(root / "refs")
            _fixture(root, "example_mist_stone", "shallow_stone", "deepslate", DEEP_IS_REQUIRED)
            a = run(root / "plans", root / "out", "example_mist_stone (declared shallow, attached deepslate)")
            print()

            print("REVERSE FIXTURE B: a note that forbids the reference it is attached to")
            root = scratch / "b"
            _write_refs(root / "refs")
            _fixture(root, "example_shallow_stone", "shallow_stone", "stone", SHALLOW_IS_FORBIDDEN)
            b = run(root / "plans", root / "out", "example_shallow_stone (shallow stone, note forbids the shallow stone)")
            print()

            print("REVERSE FIXTURE C: a note claiming another plan's reference, falsely")
            root = scratch / "c"
            _write_refs(root / "refs")
            _fixture(root, "example_shallow_stone", "shallow_stone", "stone", SHALLOW_IS_FORBIDDEN)
            _fixture(
                root, "example_starfall_ore", "ore_deposit", "deepslate",
                "This is the same reference example_shallow_stone uses, so the ore's rock is the family's rock",
            )
            c = run(root / "plans", root / "out", "example_starfall_ore (claims example_shallow_stone's reference)")

            every = a >= 1 and b >= 1 and c >= 1
            print()
            print("fault gates: %s" % (
                "all three went red as required" if every else "ONE DID NOT FIRE"
            ))
            return 0 if every else 1

        plans = build_fixture_tree(scratch)
        problems = run(plans, scratch / "out", "the four plans from the real project")
        print()
        print("Expected: example_mist_stone, example_shallow_stone and example_starfall_ore refused; example_deep_stone clean.")
        print("The engine cannot fix these -- they are contradictions in the plans, and the")
        print("author has to decide what each asset actually is.")
        return 0 if problems == 3 else 1
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
