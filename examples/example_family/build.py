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

from dataclasses import replace

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mc_art.asset_groups import build_catalogue  # noqa: E402
from mc_art.pipeline import GenerationPipeline  # noqa: E402
from mc_art.planfile import plan_from_file, reference_pool  # noqa: E402
from mc_art.style import accent_audit, band_report, family_axes, family_axes_summary  # noqa: E402

# The vanilla art this family is allowed to learn from. Every entry is a
# vanilla group; nothing here comes from a mod.
#
# `raw_iron` is in here because the raw lump has a vanilla counterpart. Its
# absence was the user's first complaint about the first delivery -- "the raw one
# doesn't reference raw iron at all" -- and they were right: a shape was invented
# for an object the game already draws. Every member of this family now has a
# vanilla authority for its shape.
REFERENCE_GROUPS = {
    "stone": "minecraft:block/stone",
    "deepslate": "minecraft:block/deepslate",
    "iron_ore": "minecraft:block/iron_ore",
    "iron_ingot": "minecraft:item/iron_ingot",
    "raw_iron": "minecraft:item/raw_iron",
}

# Reference row for the sheet, then the generated family, in family order.
REFERENCE_ROW = ("stone", "deepslate", "iron_ore", "iron_ingot", "raw_iron")
FAMILY = (
    "example_stone",
    "example_deepslate",
    "example_ore",
    "example_deepslate_ore",
    "example_raw_ore",
    "example_ingot",
)

# Which vanilla texture each member takes its *shape* from. Printing and
# asserting this is what keeps "does it reference something?" from becoming a
# silent property of a JSON field nobody reads.
SHAPE_AUTHORITY = {
    "example_stone": "stone",
    "example_deepslate": "deepslate",
    "example_ore": "stone",
    "example_deepslate_ore": "deepslate",
    "example_raw_ore": "raw_iron",
    "example_ingot": "iron_ingot",
}

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
        # Everything the reference root holds, attached or not. The renderer
        # never samples from the pool; it is there so "iron_ore.png was on disk
        # and you did not attach it" is something the engine can say.
        plan = replace(plan, available_references=reference_pool(HERE / "refs"))
        run = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0).run(
            plan, out / name, package=False
        )
        if run.sprite_path is None or not run.validation.passed:
            failures.append(
                "%s: %s" % (name, "; ".join(run.validation.errors) or "no sprite was written")
            )
        # The quality loop is what normally writes this panel; here the caller
        # is the script, so it asks for it directly. It is a diagnostic, not a
        # gate: it shows the source, the delivered sprite and the pixels that
        # changed between them, which is how "the contour was kept and only the
        # colour moved" can be seen rather than asserted. A member with no
        # reference has nothing to compare against and correctly produces no
        # file -- and this family now has no such member.
        comparison = None
        if run.sprite_path is not None and plan.references:
            try:
                comparison = GenerationPipeline.reference_comparison(
                    out / name, run.sprite_path, plan.references
                )
            except (OSError, ValueError, KeyError):
                comparison = None
        selection_path = out / name / "reference_selection.json"
        selection: dict = {}
        if selection_path.exists():
            try:
                selection = json.loads(selection_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                selection = {}
        pool_report_path = out / name / "validation_reference_pool.json"
        unused_same_class: list[str] = []
        if pool_report_path.exists():
            try:
                pool_metrics = json.loads(
                    pool_report_path.read_text(encoding="utf-8")
                ).get("metrics", {})
                unused_same_class = [
                    item for item in str(pool_metrics.get("unused_names", "")).split(",") if item
                ]
            except (OSError, ValueError):
                unused_same_class = []
        results[name] = {
            "plan": str(plan_path),
            "out": str(out / name),
            "selection": selection,
            "attached": [reference.name for reference in plan.references],
            "attached_paths": {
                reference.name: reference.path for reference in plan.references
            },
            "unused_same_class": unused_same_class,
            "sprite": str(run.sprite_path) if run.sprite_path else None,
            "validation_passed": bool(run.validation.passed),
            "comparison": bool(comparison),
            "declared_accent_budget": plan.appearance.accent_budget,
            "declared_min_cluster": plan.appearance.accent_min_cluster,
            "declared_accent_colors": list(plan.appearance.accent_colors),
            "accent_points": run.validation.metrics.get("style.accent_points"),
            "structure": run.validation.metrics.get("style.accent_structure_ok"),
            "declared_base_colors": [
                plan.appearance.palette.get(token, token)
                for style in plan.appearance.parts.values()
                for token in style.colors
            ],
        }
    return results, failures


def measure(results: dict[str, dict]) -> dict:
    """Re-measure the delivered PNGs, not the intentions behind them.

    Each member is audited with *its own* declared accent swatches. The ore's
    accent is vanilla iron_ore's tan, because the specks are iron_ore's own
    pixels, so auditing it against the family's amber would count almost nothing
    and then complain about the structure of what it did not count.
    """
    sprites = [results[name]["sprite"] for name in FAMILY if results[name]["sprite"]]
    family_accents: list[str] = []
    for name in FAMILY:
        row = results[name]
        for colour in row.get("declared_accent_colors") or []:
            if colour not in family_accents:
                family_accents.append(colour)
        if not row["sprite"]:
            continue
        row["bands"] = band_report(row["sprite"])
        # The renderer knows which pixels it placed, so the audit is told rather
        # than left to re-derive them by colour distance: an embedded accent has
        # been pulled toward its base, and the swatch test would lose it.
        declared_points = row.get("accent_points")
        # Re-measure the delivered PNG, but do not re-gate it: `validate_style`
        # already judged this sprite against the plan's OWN declared thresholds,
        # and re-judging it here with the engine defaults would report a failure
        # for a plan that deliberately declared something else. The build prints
        # the numbers; the render's verdict is the verdict.
        row["accent"] = accent_audit(
            row["sprite"],
            accent_colors=row.get("declared_accent_colors") or ACCENT_SWATCHES,
            base_colors=row["declared_base_colors"],
            budget=row["declared_accent_budget"],
            minimum_cluster=row["declared_min_cluster"],
            points=(
                {(int(x), int(y)) for x, y in declared_points}
                if declared_points else None
            ),
            edge_max=None,
            motif_repeat_max=None,
            layout_min_size_cv=None,
            layout_min_spacing_cv=None,
            ramp_min_pixels=None,
            ramp_min_levels=None,
            ramp_max_dominant_share=None,
            ramp_min_monotone=None,
            bar_fill_max=None,
            bar_min_aspect=None,
            accent_base_gap_max=None,
            accent_base_edge_mean_max=None,
        )
    return family_axes(
        sprites,
        accent_colors=family_accents or ACCENT_SWATCHES,
        base_colors=results["example_stone"]["declared_base_colors"],
    )


def contact_sheet(results: dict[str, dict], path: Path, scale: int = 10) -> Path:
    """Sources, then the family they produced -- with the source column honest.

    The first version of this sheet put every reference the script had extracted
    into one "VANILLA SOURCE" block, next to products that had not used most of
    them. It read as "these were the references" when `iron_ore.png` and
    `raw_iron.png` had never been attached to the plan at all; the Lead was
    misled by it, and so was I. So the sheet now separates three things:

    * **ATTACHED SOURCE** -- the references each member's plan actually attached,
      one column per member, which is the only thing that can honestly sit next
      to a product;
    * **AVAILABLE, NOT USED** -- what was on the reference root and was not
      attached, so the gap is visible instead of hidden;
    * **GENERATED**.
    """
    tile = 16 * scale
    gap = 10
    label_height = 16
    header_height = 22
    family_tiles = [Path(results[name]["sprite"]) for name in FAMILY]

    attached_columns: list[tuple[str, Path]] = []
    for name in FAMILY:
        # EVERY reference the plan attached, not just the globally-selected one:
        # the ore's base comes from stone and its deposits from iron_ore, and a
        # label naming only the winner would hide half of what was used.
        for reference in results[name].get("attached") or []:
            attached_columns.append((
                "%s <- %s" % (name, reference),
                Path(results[name]["attached_paths"][reference]),
            ))
    unused_rows: list[tuple[str, list[Path]]] = []
    for name in FAMILY:
        pool = {
            pool_name: HERE / "refs" / (pool_name + ".png")
            for pool_name in REFERENCE_ROW
        }
        unused = results[name].get("unused_same_class") or []
        unused_rows.append((
            name,
            [pool[item] for item in unused if item in pool and pool[item].exists()],
        ))
    unused_columns = [
        (name, image) for name, images in unused_rows for image in images
    ]

    columns = len(attached_columns) + len(unused_columns) + len(family_tiles)
    width = columns * tile + (columns + 1) * gap
    height = header_height + tile + label_height + gap
    sheet = Image.new("RGBA", (width, height), (24, 24, 28, 255))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((gap, 4), "ATTACHED SOURCE (what each plan actually used)",
              fill=(150, 200, 150, 255), font=font)
    if unused_columns:
        unused_left = gap + (len(attached_columns) + 1) * tile + len(attached_columns) * gap
        draw.text((unused_left, 4), "AVAILABLE, NOT USED",
                  fill=(240, 170, 170, 255), font=font)
    family_left = (
        gap + (len(attached_columns) + len(unused_columns) + 1) * tile
        + (len(attached_columns) + len(unused_columns)) * gap
    )
    draw.text((family_left, 4), "GENERATED (one hue axis, one accent hue)",
              fill=(214, 190, 140, 255), font=font)

    def place(index: int, image: Path, label: str, accent_label: str = "",
              tint: tuple[int, int, int, int] = (230, 230, 230, 255)) -> None:
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
        draw.text((left, header_height + tile + 2), label, fill=tint, font=font)
        if accent_label:
            draw.text((left + len(label) * 6 + 6, header_height + tile + 2), accent_label,
                      fill=(214, 190, 140, 255), font=font)

    index = 0
    for name, image in attached_columns:
        place(index, image, name, tint=(190, 230, 190, 255))
        index += 1
    for name, image in unused_columns:
        place(index, image, "%s: %s UNUSED" % (name.replace("example_", ""), image.stem),
              tint=(240, 180, 180, 255))
        index += 1
    for offset, name in enumerate(FAMILY):
        row = results[name]
        accent = row.get("accent") or {}
        place(
            index + offset,
            Path(row["sprite"]),
            name,
            "accent %s/%s" % (accent.get("accent_pixels"), accent.get("budget")),
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG")
    return path


def write_file_index(out: Path, results: dict[str, dict], sheet: Path) -> Path:
    """Describe what is actually on disk, read back from disk.

    A hand-written file list is a claim, and this one was wrong the first time:
    it named a per-member panel that the pipeline run does not write. The index
    is therefore generated by walking the output directory after the run, so the
    document cannot describe a file that is not there.
    """
    purposes = {
        "sprite.png": "the deliverable, RGBA, 16x16",
        "preview.png": "the deliverable at 24x, nearest neighbour",
        "preview_checker.png": "the deliverable at 24x on a checkerboard, so transparent edges are visible",
        "reference_comparison.png": "source / generated / changed, side by side",
        "reference_comparison.json": "the numbers behind that panel",
        "style_report.json": "value bands and accent spend measured on the delivered PNG",
        "reference_selection.json": "which reference was used, why, and what lost",
        "validation_style.json": "the art gates the plan declared, and whether they passed",
        "validation.json": "every validation stage aggregated",
        "audit.json": "the whole run: status, sprite, attempts",
        "flow_audit.json": "what was handed from each stage to the next",
        "appearance.json": "the appearance the run actually used",
        "geometry.json": "the geometry the run actually used",
        "references.json": "the reference list the run was given",
        "texture_audit.json": "generated-vs-source texture comparison",
    }
    lines = [
        "# File index",
        "",
        "Generated by `python examples/example_family/build.py` by walking this",
        "directory after the run. Nothing here is hand-written, so it cannot name a",
        "file that does not exist. Regenerate it the same way.",
        "",
        "## Top level",
        "",
        "| file | what it is |",
        "|---|---|",
        "| `%s` | vanilla sources on the left, the generated family on the right |" % sheet.name,
        "| `family_report.json` | the family-wide axes and each member's row |",
        "| `FILE_INDEX.md` | this file |",
        "",
        "## Per member",
        "",
    ]
    for name in FAMILY:
        directory = out / name
        present = sorted(
            path.name for path in directory.iterdir() if path.is_file()
        ) if directory.is_dir() else []
        lines.append("### `%s/`" % name)
        lines.append("")
        row = results.get(name) or {}
        lines.append("- run: %s; reference panel: %s" % (
            "PASS" if row.get("validation_passed") else "FAIL",
            "`reference_comparison.png`" if row.get("comparison") else "none (this member has no reference)",
        ))
        lines.append("")
        lines.append("| file | what it is |")
        lines.append("|---|---|")
        for filename in present:
            lines.append("| `%s` | %s |" % (filename, purposes.get(filename, "pipeline run artifact")))
        lines.append("")
    target = out / "FILE_INDEX.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


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

    # Every member must have taken its shape from a vanilla counterpart. The
    # engine writes this fact on every run; printing and asserting it is what
    # stops "does it reference something?" from being a property of a JSON field
    # nobody reads -- which is exactly how the first delivery shipped a raw ore
    # with no reference and nobody noticed until the user did.
    print()
    reference_problems: list[str] = []
    for name in FAMILY:
        row = results[name]
        selection = row.get("selection") or {}
        chosen = (selection.get("chosen") or {}).get("name")
        wanted = SHAPE_AUTHORITY[name]
        print("%-18s shape authority: %-11s (step=%s, offered=%s)"
              % (name, chosen or "NONE", selection.get("step"),
                 (selection.get("counts") or {}).get("offered")))
        if chosen is None:
            reference_problems.append(
                "%s took no shape authority at all (step=%s): %s"
                % (name, selection.get("step"), selection.get("step_detail"))
            )
        elif chosen != wanted:
            reference_problems.append(
                "%s took its shape from %r, not the declared authority %r"
                % (name, chosen, wanted)
            )

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

    index_path = write_file_index(out, results, sheet)
    print("INDEX  -> %s" % index_path)

    problems = list(failures)
    problems.extend(reference_problems)
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
        below_minimum = (results["example_ore"].get("accent") or {}).get(
            "below_minimum_pixels", 0
        )
        print()
        print("ORE BASE   %d pixel(s) differ from example_stone; the ore declared %s accent pixel(s)"
              % (len(differing), accent_pixels))
        # The deposits, and nothing else: a handful of slack covers the stray
        # specks `accent_cleanup` repainted with the nearest base colour, which
        # differ from the stone without being accent pixels any more.
        if not (accent_pixels or 0) <= len(differing) <= (accent_pixels or 0) + 8:
            problems.append(
                "example_ore's base drifted from example_stone: %d differing pixel(s) against "
                "%s accent pixel(s)"
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
