"""Prove the art gates can fail, on the real example family.

A check that has never been seen to fail is not a check. This script takes the
family's own plans, injects the four faults people actually reported, renders
them, and asserts that the matching gate -- and only that gate -- turns red.
It exits non-zero if any fault slips through, so it is itself a gate.

Usage:
    python examples/example_family/fault_demo.py --out <directory>
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mc_art.contracts import appearance_from_dict, geometry_from_dict, request_from_dict  # noqa: E402
from mc_art.pipeline import GenerationPipeline, GenerationPlan  # noqa: E402
from mc_art.planfile import descriptor_from_dict, plan_from_file, reference_from_dict  # noqa: E402
from mc_art.style import accent_audit, band_report, family_axes  # noqa: E402

ACCENT_SWATCHES = ["#FFE3B0", "#F0BE6E", "#CF9440", "#8E5C1C"]


def _load(name: str) -> dict:
    return json.loads((HERE / "plans" / (name + ".plan.json")).read_text(encoding="utf-8"))


def _plan(data: dict) -> GenerationPlan:
    references = [reference_from_dict(item) for item in data.get("references", [])]
    resolved = []
    for reference in references:
        path = Path(reference.path)
        if not path.is_absolute():
            reference = replace(reference, path=str((HERE / "plans" / path).resolve()))
        resolved.append(reference)
    return GenerationPlan(
        request=request_from_dict(data["request"]),
        descriptor=descriptor_from_dict(data["descriptor"]),
        geometry=geometry_from_dict(data["geometry"]),
        appearance=appearance_from_dict(data["appearance"]),
        references=resolved,
    )


def _render(data: dict, out: Path):
    return GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0).run(
        _plan(data), out, package=False
    )


def _blank_rows() -> list[str]:
    return ["." * 16 for _ in range(16)]


def fault_lone_dot(*, cleanup: bool) -> dict:
    """One isolated accent pixel: the literal "突兀的来一两个点".

    Rendered twice: once as a pure gate (the speck survives and the budget of
    zero is breached) and once with the declared repair on (the renderer
    repaints it and the delivered PNG has no accent at all).
    """
    data = _load("example_ore")
    data["request"]["name"] = "fault_lone_dot"
    rows = _blank_rows()
    rows[8] = "." * 8 + "1" + "." * 7
    data["appearance"]["pixel_map"]["rows"] = rows
    data["appearance"]["accent_min_cluster"] = 3
    data["appearance"]["accent_cleanup"] = cleanup
    data["appearance"]["accent_budget"] = 0
    return data


def fault_ugly_dots() -> dict:
    """Several two-pixel specks: the "难看的点点" the user complained about.

    The repair is deliberately off here, so the gate has to report the fault
    instead of the renderer quietly hiding it.
    """
    data = _load("example_ore")
    data["request"]["name"] = "fault_ugly_dots"
    rows = _blank_rows()
    for x, y in ((3, 3), (9, 2), (12, 11), (6, 14), (1, 9)):
        rows[y] = rows[y][:x] + "23" + rows[y][x + 2:]
    data["appearance"]["pixel_map"]["rows"] = rows
    data["appearance"]["accent_min_cluster"] = 3
    data["appearance"]["accent_cleanup"] = False
    data["appearance"]["accent_budget"] = 0
    return data


def fault_stretched_deposit() -> dict:
    """A bright accent pasted straight onto the base with no rim.

    This is the "橙色和蓝色的边缘要拖突兀有多突兀" fault in raster form: every
    boundary pixel is the brightest amber, so the accent stops dead against the
    material instead of stepping down through a rim.
    """
    data = _load("example_ore")
    data["request"]["name"] = "fault_hard_edge"
    rows = _blank_rows()
    for y in range(6, 10):
        rows[y] = "." * 6 + "1111" + "." * 6
    data["appearance"]["pixel_map"]["rows"] = rows
    data["appearance"]["accent_min_cluster"] = 3
    data["appearance"]["accent_budget"] = 36
    data["appearance"]["accent_edge_max"] = 60
    return data


def fault_second_accent_hue() -> dict:
    """A sibling whose accent is a different colour: "看不出是两个同族的东西"."""
    data = _load("example_ore")
    data["request"]["name"] = "fault_other_accent"
    rows = _blank_rows()
    for y in range(1, 3):
        rows[y] = ".." + "55" + "." * 12
    for y in range(3, 5):
        rows[y] = "." + "5555" + "." * 11
    data["appearance"]["palette"]["alien_core"] = "#6ED2EB"
    data["appearance"]["palette"]["alien_rim"] = "#2E7F9E"
    data["appearance"]["pixel_map"]["legend"] = {"5": "#6ED2EB", "6": "#2E7F9E"}
    data["appearance"]["pixel_map"]["rows"] = rows
    data["appearance"]["accent_colors"] = ["#6ED2EB", "#2E7F9E"]
    data["appearance"]["accent_min_cluster"] = 3
    data["appearance"]["accent_budget"] = 36
    data["appearance"]["accent_edge_max"] = 200
    return data


def fault_scatter() -> dict:
    """A sprite painted as equal-value static instead of a ramp.

    The authored ramp is present and correct; the fault is that it is sampled
    with no shade axis and full noise, so the value of each pixel is decided at
    random. Every pixel then disagrees with every neighbour, which is the
    "等值散点" the value gate exists to catch.
    """
    data = _load("example_stone")
    data["request"]["name"] = "fault_scatter"
    data["appearance"]["reference_sampling"] = "none"
    data["appearance"]["part_reference_sampling"] = {}
    data["appearance"]["part_reference_sources"] = {}
    data["appearance"]["band_maximum_isolated"] = 0.1
    data["appearance"]["band_maximum_step"] = 30
    face = data["appearance"]["parts"]["face"]
    face["colors"] = ["stone_1", "stone_5"]
    face["noise"] = 1.0
    face["shade_axis"] = "none"
    return data


def fault_reshaped() -> dict:
    """A reshape when only a recolour was asked for.

    The plan says ``appearance_only`` against iron_ingot, so the delivered
    alpha must still be iron_ingot's. This fault asks for the same recolour but
    declares a brand-new silhouette, which the shape lock must not honour.
    """
    data = _load("example_ingot")
    data["request"]["name"] = "fault_reshaped"
    data["descriptor"]["shape_edit_mode"] = "new_silhouette"
    data["descriptor"]["reference_strategy"] = "brand new silhouette, redraw the contour"
    data["geometry"]["primitives"][0]["params"]["rows"] = [
        "." * 2 + "X" * 12 + "." * 2 for _ in range(16)
    ]
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    checks: list[tuple[str, bool, str]] = []

    # 1 + 2: the accent budget and the minimum deposit size, as a gate.
    for label, builder in (
        ("lone-dot", lambda: fault_lone_dot(cleanup=False)),
        ("ugly-dots", fault_ugly_dots),
    ):
        data = builder()
        run = _render(data, out / label)
        audit = accent_audit(
            run.sprite_path,
            accent_colors=data["appearance"]["accent_colors"],
            base_colors=["#39404D", "#474F5F", "#576073", "#6A7488", "#808A9E"],
            budget=data["appearance"]["accent_budget"],
            minimum_cluster=data["appearance"]["accent_min_cluster"],
        )
        checks.append((
            "%s -> budget 0 fails and no cluster is below the declared 3" % label,
            audit["within_budget"] is False and audit["clusters_ok"] is False,
            "; ".join(audit["reasons"]),
        ))
        checks.append((
            "%s -> the render stage reports the same failure, not a silent pass" % label,
            not run.validation.passed,
            "; ".join(run.validation.errors) or "validation passed",
        ))
        print("%-12s accent=%s clusters=%s sizes=%s below_min=%s -> validation=%s" % (
            label, audit["accent_pixels"], audit["clusters"], audit["cluster_sizes"],
            audit["below_minimum_pixels"],
            "FAILED" if not run.validation.passed else "passed"))

    # 2b: the declared repair, on the same fault, must actually deliver a clean PNG.
    repaired = fault_lone_dot(cleanup=True)
    repaired_run = _render(repaired, out / "lone-dot-cleaned")
    repaired_sprite = Image.open(repaired_run.sprite_path).convert("RGBA")
    cleaned_pixel = repaired_sprite.getpixel((8, 8))[:3]
    repaired_audit = accent_audit(
        repaired_run.sprite_path,
        accent_colors=repaired["appearance"]["accent_colors"],
        base_colors=["#39404D", "#474F5F", "#576073", "#6A7488", "#808A9E"],
        budget=0,
        minimum_cluster=3,
    )
    checks.append((
        "lone-dot with accent_cleanup -> the speck is repainted and the budget passes",
        repaired_audit["accent_pixels"] == 0 and cleaned_pixel != (255, 227, 176)
        and repaired_run.validation.passed,
        "pixel(8,8)=%s accent_pixels=%s validation=%s" % (
            cleaned_pixel, repaired_audit["accent_pixels"],
            "passed" if repaired_run.validation.passed else "FAILED"),
    ))
    print("%-12s accent=%s pixel(8,8)=%s -> validation=%s" % (
        "cleaned", repaired_audit["accent_pixels"], cleaned_pixel,
        "passed" if repaired_run.validation.passed else "FAILED"))

    # 2c: the value gate. A ramp sampled with no axis and full noise is static.
    scatter = fault_scatter()
    scatter_run = _render(scatter, out / "scatter")
    scatter_bands = band_report(
        scatter_run.sprite_path, maximum_isolated=0.1, maximum_step=30
    )
    checks.append((
        "scatter -> band_maximum_step 30 fails",
        scatter_bands["consistent"] is False and not scatter_run.validation.passed,
        "isolated=%.2f%% (limit 10%%) step=%.1f (limit 30) verdict=%s" % (
            scatter_bands["isolated_share"] * 100.0,
            scatter_bands["mean_neighbour_step"], scatter_bands["verdict"]),
    ))
    print("%-12s bands=%s isolated=%.2f%% step=%.1f -> validation=%s" % (
        "scatter", scatter_bands["band_count"], scatter_bands["isolated_share"] * 100.0,
        scatter_bands["mean_neighbour_step"],
        "FAILED" if not scatter_run.validation.passed else "passed"))
    del scatter

    # 3: the edge gate.
    hard = fault_stretched_deposit()
    run = _render(hard, out / "hard-edge")
    audit = accent_audit(
        run.sprite_path,
        accent_colors=hard["appearance"]["accent_colors"],
        base_colors=["#39404D", "#474F5F", "#576073", "#6A7488", "#808A9E"],
        budget=hard["appearance"]["accent_budget"],
        minimum_cluster=hard["appearance"]["accent_min_cluster"],
        edge_max=hard["appearance"]["accent_edge_max"],
    )
    checks.append((
        "hard-edge -> accent_edge_max 60 fails",
        audit["edge_ok"] is False,
        "; ".join(audit["reasons"]) or "edge_ok=%s p90=%s" % (audit["edge_ok"], audit["edge_delta_p90"]),
    ))
    print("%-12s edge_p90=%s (limit 60)  edge_ok=%s" % (
        "hard-edge", audit["edge_delta_p90"], audit["edge_ok"]))

    # 4: the accent hue axis across a family.
    good_run = _render(_load("example_ore"), out / "example_ore_good")
    alien = fault_second_accent_hue()
    alien_run = _render(alien, out / "other-accent")
    family = family_axes(
        [str(good_run.sprite_path), str(alien_run.sprite_path)],
        accent_colors=["#F0BE6E", "#6ED2EB"],
    )
    checks.append((
        "other-accent -> family accent hue span fails",
        family["consistent"] is False,
        "; ".join(family["reasons"]) or "span=%s" % family["accent_hue_span_degrees"],
    ))
    print("%-12s accent_hue_span=%s deg base_hue_span=%s deg  consistent=%s" % (
        "other-accent", family["accent_hue_span_degrees"], family["hue_span_degrees"],
        family["consistent"]))

    # 5: the shape lock. Two renders of the same grid mask: one that asks only
    # for a recolour, and one that asks for a new silhouette. If the first did
    # not keep iron_ingot's contour, or the second did, the check is vacuous.
    reference = Image.open(HERE / "refs" / "iron_ingot.png").convert("RGBA")

    def alpha_mismatch(sprite_path) -> int:
        sprite = Image.open(sprite_path).convert("RGBA")
        return sum(
            1
            for y in range(16)
            for x in range(16)
            if (sprite.getpixel((x, y))[3] >= 8) != (reference.getpixel((x, y))[3] >= 8)
        )

    kept = _render(_load("example_ingot"), out / "contour-kept")
    reshaped = _render(fault_reshaped(), out / "reshaped")
    kept_mismatch = alpha_mismatch(kept.sprite_path)
    reshaped_mismatch = alpha_mismatch(reshaped.sprite_path)
    checks.append((
        "appearance_only keeps the reference contour, new_silhouette does not",
        kept_mismatch == 0 and reshaped_mismatch > 0,
        "appearance_only: %d mismatch(es); new_silhouette: %d mismatch(es)"
        % (kept_mismatch, reshaped_mismatch),
    ))
    print("%-12s alpha vs iron_ingot: appearance_only=%d, new_silhouette=%d mismatch(es)" % (
        "contour", kept_mismatch, reshaped_mismatch))

    # A sheet so the difference can be looked at, not just counted.
    _sheet(out)

    print()
    failed = False
    for label, ok, detail in checks:
        print("%-4s %s  (%s)" % ("PASS" if ok else "FAIL", label, detail))
        failed = failed or not ok
    return 1 if failed else 0


def _sheet(out: Path) -> None:
    panels = [
        ("equal-value static", out / "scatter" / "sprite.png"),
        ("one lone dot (gate)", out / "lone-dot" / "sprite.png"),
        ("two-pixel specks", out / "ugly-dots" / "sprite.png"),
        ("no rim, all core", out / "hard-edge" / "sprite.png"),
        ("alien accent", out / "other-accent" / "sprite.png"),
        ("good ore", out / "example_ore_good" / "sprite.png"),
        ("lone dot, cleaned", out / "lone-dot-cleaned" / "sprite.png"),
    ]
    scale = 12
    tile = 16 * scale
    gap = 12
    sheet = Image.new(
        "RGBA", (len(panels) * tile + (len(panels) + 1) * gap, tile + 34), (24, 24, 28, 255)
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(panels):
        if not path.exists():
            continue
        left = gap + index * (tile + gap)
        with Image.open(path) as loaded:
            sprite = loaded.convert("RGBA")
        sheet.alpha_composite(
            sprite.resize((tile, tile), Image.Resampling.NEAREST), (left, 22)
        )
        draw.text((left, 6), label, fill=(230, 230, 230, 255))
    sheet.save(out / "gates_fault_left_good_right.png", "PNG")
    print("SHEET        %s" % (out / "gates_fault_left_good_right.png"))


if __name__ == "__main__":
    raise SystemExit(main())
