"""Build the neutral example family: one rock, its deep variant, its ore, a raw
lump and an ingot.

The point of this example is the complaint it answers. A family that reads as
one material is not a palette histogram that overlaps; it is one shared hue and
value axis, one shared accent hue, and a delivery where nothing arrives as a
lone dot. So the script does the whole loop a person would do:

  1. pull the vanilla textures it is allowed to learn from out of a game jar;
  2. render every plan;
  3. re-measure the rendered pixels (bands, accent spend, family axes);
  4. write a side-by-side sheet and a JSON report.

Usage:
    python examples/example_family/build.py --jar <game jar> --out <directory>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mc_art.asset_groups import build_catalogue  # noqa: E402
from mc_art.pipeline import GenerationPipeline  # noqa: E402
from mc_art.planfile import plan_from_file  # noqa: E402
from mc_art.style import accent_audit, band_report, family_axes, family_axes_summary  # noqa: E402

# The vanilla art this family is allowed to learn from. Every entry is a
# vanilla group; nothing here comes from a mod.
REFERENCE_GROUPS = {
    "stone": "minecraft:block/stone",
    "deepslate": "minecraft:block/deepslate",
    "iron_ore": "minecraft:block/iron_ore",
    "iron_ingot": "minecraft:item/iron_ingot",
}

# Reference row for the sheet, then the generated family, in family order.
REFERENCE_ROW = ("stone", "deepslate", "iron_ore", "iron_ingot")
FAMILY = ("example_stone", "example_deepslate", "example_ore", "example_raw_ore", "example_ingot")

# The family's one accent hue. Every member that carries an accent carries this
# one, which is what makes the ore, the raw lump and the ingot read as the same
# metal seam rather than three unrelated colours.
ACCENT_SWATCHES = ["#FFE3B0", "#F0BE6E", "#CF9440", "#8E5C1C"]


def extract_references(jar: Path, refs: Path) -> dict[str, Path]:
    """Write vanilla textures to ``refs/<name>.png``, named the way plans expect."""
    refs.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    catalogue = build_catalogue([jar])
    try:
        for name, asset_id in REFERENCE_GROUPS.items():
            staging = refs / ("_" + name)
            produced = catalogue.extract(asset_id, staging)
            if not produced:
                raise SystemExit("the jar has no texture for %s" % asset_id)
            chosen = next(
                (path for path in produced if path.stem == name), produced[0]
            )
            target = refs / (name + ".png")
            shutil.copyfile(chosen, target)
            written[name] = target
            shutil.rmtree(staging, ignore_errors=True)
    finally:
        catalogue.close()
    return written


def render_family(out: Path) -> tuple[dict[str, dict], list[str]]:
    """Render every plan and collect its own declaration plus its verdict."""
    results: dict[str, dict] = {}
    failures: list[str] = []
    for name in FAMILY:
        plan_path = HERE / "plans" / (name + ".plan.json")
        plan = plan_from_file(plan_path)
        run = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0).run(
            plan, out / name, package=False
        )
        if run.sprite_path is None or not run.validation.passed:
            failures.append(
                "%s: %s" % (name, "; ".join(run.validation.errors) or "no sprite was written")
            )
        results[name] = {
            "plan": str(plan_path),
            "out": str(out / name),
            "sprite": str(run.sprite_path) if run.sprite_path else None,
            "validation_passed": bool(run.validation.passed),
            "declared_accent_budget": plan.appearance.accent_budget,
            "declared_min_cluster": plan.appearance.accent_min_cluster,
            "declared_base_colors": [
                plan.appearance.palette.get(token, token)
                for style in plan.appearance.parts.values()
                for token in style.colors
            ],
        }
    return results, failures


def measure(results: dict[str, dict]) -> dict:
    """Re-measure the delivered PNGs, not the intentions behind them."""
    sprites = [results[name]["sprite"] for name in FAMILY if results[name]["sprite"]]
    for name in FAMILY:
        row = results[name]
        if not row["sprite"]:
            continue
        row["bands"] = band_report(row["sprite"])
        row["accent"] = accent_audit(
            row["sprite"],
            accent_colors=ACCENT_SWATCHES,
            base_colors=row["declared_base_colors"],
            budget=row["declared_accent_budget"],
            minimum_cluster=row["declared_min_cluster"],
        )
    return family_axes(
        sprites,
        accent_colors=ACCENT_SWATCHES,
        base_colors=results["example_stone"]["declared_base_colors"],
    )


def contact_sheet(results: dict[str, dict], path: Path, scale: int = 10) -> Path:
    """One image: the vanilla sources beside the family they produced."""
    tile = 16 * scale
    gap = 10
    label_height = 16
    header_height = 22
    reference_tiles = [HERE / "refs" / (name + ".png") for name in REFERENCE_ROW]
    family_tiles = [Path(results[name]["sprite"]) for name in FAMILY]
    columns = len(reference_tiles) + len(family_tiles)
    width = columns * tile + (columns + 1) * gap
    height = header_height + tile + label_height + gap
    sheet = Image.new("RGBA", (width, height), (24, 24, 28, 255))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((gap, 4), "VANILLA SOURCE", fill=(150, 150, 158, 255), font=font)
    family_left = gap + (len(reference_tiles) + 1) * tile + len(reference_tiles) * gap
    draw.text((family_left, 4), "GENERATED FAMILY (one hue axis, one accent hue)",
              fill=(214, 190, 140, 255), font=font)

    def place(index: int, image: Path, label: str, accent_label: str = "") -> None:
        left = gap + index * (tile + gap)
        with Image.open(image) as loaded:
            sprite = loaded.convert("RGBA")
        # Transparent item sprites need a checker to be visible at all.
        backdrop = Image.new("RGBA", (tile, tile), (44, 44, 50, 255))
        checker = ImageDraw.Draw(backdrop)
        for cy in range(0, tile, scale):
            for cx in range(0, tile, scale):
                if (cx // scale + cy // scale) % 2 == 0:
                    checker.rectangle([cx, cy, cx + scale - 1, cy + scale - 1],
                                      fill=(56, 56, 62, 255))
        backdrop.alpha_composite(sprite.resize((tile, tile), Image.Resampling.NEAREST))
        sheet.alpha_composite(backdrop, (left, header_height))
        draw.text((left, header_height + tile + 2), label, fill=(230, 230, 230, 255), font=font)
        if accent_label:
            draw.text((left + len(label) * 6 + 6, header_height + tile + 2), accent_label,
                      fill=(214, 190, 140, 255), font=font)

    for index, (name, image) in enumerate(zip(REFERENCE_ROW, reference_tiles)):
        place(index, image, name)
    for offset, name in enumerate(FAMILY):
        row = results[name]
        accent = row.get("accent") or {}
        place(
            len(reference_tiles) + offset,
            Path(row["sprite"]),
            name,
            "accent %s/%s" % (accent.get("accent_pixels"), accent.get("budget")),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", required=True, help="a game jar holding assets/minecraft")
    parser.add_argument("--out", required=True, help="output root for the rendered family")
    parser.add_argument("--sheet", help="contact sheet path; defaults to "
                        "<out>/family_compare_reference_left_generated_right.png")
    parser.add_argument("--no-extract", action="store_true",
                        help="reuse refs/ instead of pulling them out of the jar again")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if not args.no_extract:
        written = extract_references(Path(args.jar), HERE / "refs")
        for name, path in written.items():
            print("REFERENCE %-12s %s" % (name, path))

    results, failures = render_family(out)
    family = measure(results)

    print()
    for name in FAMILY:
        row = results[name]
        accent = row.get("accent") or {}
        bands = row.get("bands") or {}
        print("%-18s -> %s" % (name, row["sprite"]))
        print("   validation=%s  bands=%s isolated=%.2f%%  accent=%s/%s in %s cluster(s)"
              % (
                  "PASS" if row["validation_passed"] else "FAIL",
                  bands.get("band_count"),
                  float(bands.get("isolated_share", 0.0)) * 100.0,
                  accent.get("accent_pixels"),
                  "unchecked" if accent.get("budget") is None else accent.get("budget"),
                  accent.get("clusters"),
              ))
        for reason in accent.get("reasons", []):
            print("   FAIL %s" % reason)
    print()
    print(family_axes_summary(family))
    for reason in family["reasons"]:
        print("   FAIL %s" % reason)

    sheet = contact_sheet(
        results,
        Path(args.sheet) if args.sheet
        else out / "family_compare_reference_left_generated_right.png",
    )
    report = {
        "references": {name: str(HERE / "refs" / (name + ".png")) for name in REFERENCE_ROW},
        "family": {name: {key: value for key, value in results[name].items() if key != "plan"}
                   for name in FAMILY},
        "axes": family,
        "sheet": str(sheet),
    }
    report_path = out / "family_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print("SHEET  -> %s" % sheet)
    print("REPORT -> %s" % report_path)

    problems = list(failures)
    problems.extend(
        "%s: %s" % (name, reason)
        for name in FAMILY
        for reason in (results[name].get("accent") or {}).get("reasons", [])
    )
    if not family["consistent"]:
        problems.extend(family["reasons"])

    # The ore's base face is supposed to be example_stone's own face, not a
    # re-render that happens to look similar. Only the declared accent pixels
    # may differ; anything else means the two drifted apart.
    stone_path = results["example_stone"]["sprite"]
    ore_path = results["example_ore"]["sprite"]
    if stone_path and ore_path:
        stone_sprite = Image.open(stone_path).convert("RGBA")
        ore_sprite = Image.open(ore_path).convert("RGBA")
        differing = {
            (x, y)
            for y in range(16)
            for x in range(16)
            if stone_sprite.getpixel((x, y)) != ore_sprite.getpixel((x, y))
        }
        accent_pixels = (results["example_ore"].get("accent") or {}).get("accent_pixels")
        print()
        print("ORE BASE   %d pixel(s) differ from example_stone; the ore declared %s accent pixel(s)"
              % (len(differing), accent_pixels))
        if len(differing) != accent_pixels:
            problems.append(
                "example_ore's base drifted from example_stone: %d differing pixel(s) against %s accent pixel(s)"
                % (len(differing), accent_pixels)
            )

    if problems:
        print()
        for problem in problems:
            print("PROBLEM %s" % problem)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
