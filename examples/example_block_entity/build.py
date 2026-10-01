"""Build the block-entity example: a desk with a sheet of paper on it.

A block entity is not a recoloured plank. It is several boxes, and each face
samples its own rectangle of a texture painted for that face. This script proves
the difference by building the same desk twice, and rendering both:

* **correct** -- the sheet face declares ``uv [0,0,10,8]`` for a 10x8 face, so it
  samples a sheet-shaped rectangle of the paper texture. The audit passes.
* **faulted** -- the sheet face declares no ``uv`` (or the whole tile). A 10x8
  face then inherits all 16x16 of whatever texture is bound to it, which is
  exactly how "a sheet of paper on a dark wood desk" comes out as more wood.
  The audit fails, and the render shows it.

Usage:
    python examples/example_block_entity/build.py --out <directory>
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mc_art.blockmodel import audit_block_model, render_block_spec, write_block_model  # noqa: E402


def make_textures(directory: Path) -> dict[str, Path]:
    """Two 16x16 tiles, generated here so the example needs no external art."""
    directory.mkdir(parents=True, exist_ok=True)
    planks = Image.new("RGBA", (16, 16), (94, 64, 36, 255))
    draw = ImageDraw.Draw(planks)
    for row in range(0, 16, 4):
        draw.line((0, row, 15, row), fill=(62, 40, 20, 255))
        for x in range(0, 16, 6):
            planks.putpixel((x, row + 1), (118, 82, 46, 255))
    for y in range(16):
        for x in range(16):
            if (x * 5 + y * 3) % 11 == 0:
                red, green, blue, alpha = planks.getpixel((x, y))
                planks.putpixel((x, y), (max(0, red - 14), max(0, green - 12), max(0, blue - 10), alpha))
    plank_path = directory / "example_planks.png"
    planks.save(plank_path)

    paper = Image.new("RGBA", (16, 16), (222, 214, 190, 255))
    for y in range(16):
        for x in range(16):
            if (x + y) % 7 == 0:
                paper.putpixel((x, y), (204, 196, 172, 255))
    for x in range(2, 14):
        paper.putpixel((x, 6), (168, 160, 140, 255))
    paper_path = directory / "example_paper.png"
    paper.save(paper_path)
    return {"wood": plank_path, "paper": paper_path}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    spec = json.loads((HERE / "desk.json").read_text(encoding="utf-8"))
    make_textures(HERE / "textures")

    # Two faults, both of them the same mistake seen from two sides:
    #   * the sheet given no uv at all -> a 10x8 face inherits all 16x16 texels;
    #   * the sheet bound to the wood texture with no uv -> literally "just
    #     changed the planks a bit", which is what the delivered desk was.
    faulted = copy.deepcopy(spec)
    faulted["name"] = "example_desk_no_uv"
    faulted["elements"][3]["faces"] = {"up": {"texture": "paper"}}

    planks_fault = copy.deepcopy(spec)
    planks_fault["name"] = "example_desk_planks_sheet"
    planks_fault["elements"][3]["faces"] = {"up": {"texture": "wood"}}

    good_audit = audit_block_model(spec, base_dir=HERE)
    bad_audit = audit_block_model(faulted, base_dir=HERE)
    planks_audit = audit_block_model(planks_fault, base_dir=HERE)
    print("correct model: %s (%s), %d element(s), %d face(s)"
          % (good_audit["kind"], "PASS" if good_audit["passed"] else "FAIL",
             good_audit["elements"], good_audit["faces"]))
    for label, report in (("no uv", bad_audit), ("sheet bound to wood", planks_audit)):
        print("faulted model (%s): %s" % (label, "PASS" if report["passed"] else "FAIL"))
        for problem in report["problems"]:
            print("   PROBLEM %s" % problem)

    written = write_block_model(spec, out / "pack", base_dir=HERE)
    print("PACK         %s" % written["pack"])
    print("UV_MAP       %s" % written["uv_map"])

    views = ("front34", "side")
    rendered = render_block_spec(spec, written["pack"], out / "desk_views.png", views=views)
    print("VIEWS        %s" % rendered["image"])

    # Render both faults too. It is worth looking at: the numbers say
    # "stretched", the image shows what that means.
    panels = [("sheet uv declared [0,0,10,8]  -> audit PASS", rendered["image"], (210, 230, 210, 255))]
    for label, spec_variant, colour in (
        ("sheet uv omitted  -> audit FAIL", faulted, (240, 190, 180, 255)),
        ("sheet bound to wood, no uv  -> audit FAIL", planks_fault, (240, 190, 180, 255)),
    ):
        faulted_pack = write_block_model(spec_variant, out / ("pack_" + spec_variant["name"]), base_dir=HERE)
        view = render_block_spec(
            spec_variant, faulted_pack["pack"], out / (spec_variant["name"] + "_view.png"),
            views=("front34",),
        )
        panels.append((label, view["image"], colour))

    panel_width, panel_height = 360, 320
    compare = Image.new(
        "RGBA", (panel_width * len(panels) + 12 * (len(panels) - 1), panel_height + 26),
        (24, 24, 28, 255),
    )
    draw = ImageDraw.Draw(compare)
    for index, (label, path, colour) in enumerate(panels):
        left = index * (panel_width + 12)
        with Image.open(path) as loaded:
            panel = loaded.convert("RGBA").crop((0, 0, panel_width, panel_height))
        draw.text((left + 6, 6), label, fill=colour)
        compare.alpha_composite(panel, (left, 24))
    target = out / "desk_uv_fault_left_correct_right.png"
    compare.save(target, "PNG")
    print("COMPARE      %s" % target)

    if not good_audit["passed"] or bad_audit["passed"] or planks_audit["passed"]:
        print("GATE FAILED: the correct model must pass and both faults must not")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
