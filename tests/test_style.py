"""Tests for the art-quality gates: value bands, accent budget, family axes.

Each gate is proved twice on purpose. A check that only ever passes is not a
check, so every test here builds the good input and the faulted input and
asserts the verdict flips between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mc_art import style
from mc_art.contracts import (
    AppearanceSpec,
    AssetForm,
    AssetRequest,
    GeometrySpec,
    PartAppearance,
    PartSpec,
    PrimitiveSpec,
    ShapeDescriptor,
)
from mc_art.pipeline import GenerationPipeline, GenerationPlan
from mc_art.validation import validate_style

FULL_FACE = [
    "XXXXXXXXXXXXXXXX",
] * 16


def _solid(path: Path, colour: tuple[int, int, int, int], size: tuple[int, int] = (16, 16)) -> Path:
    Image.new("RGBA", size, colour).save(path)
    return path


def _speckled(path: Path, base: tuple[int, int, int, int], specks: list[tuple[int, int]], accent=(242, 192, 112, 255)) -> Path:
    image = Image.new("RGBA", (16, 16), base)
    for x, y in specks:
        image.putpixel((x, y), accent)
    image.save(path)
    return path


def _gradient(path: Path, top=(150, 168, 196, 255), bottom=(24, 28, 36, 255)) -> Path:
    image = Image.new("RGBA", (16, 16), top)
    for y in range(16):
        ratio = y / 15.0
        colour = tuple(
            round(top[channel] + (bottom[channel] - top[channel]) * ratio) for channel in range(3)
        ) + (255,)
        for x in range(16):
            image.putpixel((x, y), colour)
    image.save(path)
    return path


def _block_plan(tmp_path: Path, appearance: AppearanceSpec) -> GenerationPlan:
    part = PartSpec(id="face", meaning="the whole face", style_role="silhouette support",
                    contour_intent="surface")
    return GenerationPlan(
        request=AssetRequest(
            query="example block face",
            form=AssetForm.CUSTOM,
            name="example_block",
            namespace="examplepack",
            width=16,
            height=16,
        ),
        descriptor=ShapeDescriptor(
            target="example block face",
            semantic="a 16x16 block face",
            visual_identity=["16x16 opaque block face"],
            parts=[part],
        ),
        geometry=GeometrySpec(
            width=16,
            height=16,
            parts=[part],
            primitives=[PrimitiveSpec(
                id="face_mask",
                part_id="face",
                primitive="custom_mask",
                params={"offset": [0, 0], "marker": "X", "rows": FULL_FACE},
            )],
            background_transparent=False,
        ),
        appearance=appearance,
        references=[],
    )


# -- value bands: a ramp, not scatter ----------------------------------------


def test_a_gradient_reads_as_banded_and_a_speckled_fill_does_not(tmp_path: Path) -> None:
    smooth = _gradient(tmp_path / "smooth.png")
    speckled = _speckled(tmp_path / "speckled.png", (60, 66, 78, 255),
                         [(3, 3), (9, 2), (12, 11), (6, 14)])
    good = style.band_report(smooth, maximum_isolated=0.01)
    bad = style.band_report(speckled, maximum_isolated=0.01)
    assert good["consistent"] is True, good
    assert good["isolated_pixels"] == 0
    assert good["band_count"] >= 8, "a ramp should occupy many value bands"
    assert bad["consistent"] is False, bad
    assert bad["isolated_pixels"] == 4
    assert bad["verdict"] == "scattered"


# -- accent budget: declarable, and assertable -------------------------------


def test_an_accent_budget_of_zero_passes_plain_stone_and_fails_one_amber_pixel(tmp_path: Path) -> None:
    plain = _solid(tmp_path / "plain.png", (60, 66, 78, 255))
    with_speck = _speckled(tmp_path / "speck.png", (60, 66, 78, 255), [(8, 8)])
    base = ["#3C424E", "#565D6B"]
    good = style.accent_audit(plain, base_colors=base, budget=0)
    bad = style.accent_audit(with_speck, base_colors=base, budget=0)
    assert good["accent_pixels"] == 0 and good["consistent"] is True, good
    assert bad["accent_pixels"] == 1 and bad["consistent"] is False, bad
    assert "exceed the declared budget of 0" in bad["reasons"][0]


def test_the_budget_is_the_only_thing_that_makes_it_a_failure(tmp_path: Path) -> None:
    with_speck = _speckled(tmp_path / "speck.png", (60, 66, 78, 255), [(8, 8)])
    unchecked = style.accent_audit(with_speck, base_colors=["#3C424E"])
    assert unchecked["accent_pixels"] == 1
    assert unchecked["consistent"] is True, "no budget declared means nothing is claimed"


def test_a_declared_minimum_cluster_removes_a_speck_and_keeps_a_deposit(tmp_path: Path) -> None:
    image = Image.new("RGBA", (16, 16), (60, 66, 78, 255))
    image.putpixel((2, 2), (242, 192, 112, 255))
    for x in range(8, 12):
        for y in range(9, 11):
            image.putpixel((x, y), (242, 192, 112, 255))
    source = tmp_path / "mixed.png"
    image.save(source)
    points, mode = style.accent_points(source, accent_colors=["#F2C070"], base_colors=["#3C424E"])
    assert mode == "declared" and len(points) == 9
    cleaned, kept, removed = style.despeckle_accent(source, points, 4)
    assert removed == 1
    assert len(kept) == 8
    assert cleaned.getpixel((2, 2))[:3] == (60, 66, 78), "the speck takes the base material's pixel"
    assert cleaned.getpixel((9, 9))[:3] == (242, 192, 112), "the authored deposit survives"


def test_accent_edge_reports_the_step_from_the_accent_to_its_base(tmp_path: Path) -> None:
    soft = tmp_path / "soft.png"
    image = Image.new("RGBA", (16, 16), (60, 66, 78, 255))
    for x in range(6, 10):
        image.putpixel((x, 8), (110, 118, 134, 255))
    image.save(soft)
    harsh = tmp_path / "harsh.png"
    image = Image.new("RGBA", (16, 16), (60, 66, 78, 255))
    for x in range(6, 10):
        image.putpixel((x, 8), (250, 200, 60, 255))
    image.save(harsh)
    gentle = style.accent_audit(soft, base_colors=["#3C424E"], budget=4, edge_max=60)
    abrupt = style.accent_audit(harsh, base_colors=["#3C424E"], budget=4, edge_max=60)
    assert gentle["edge_ok"] is True, gentle
    assert abrupt["edge_ok"] is False, abrupt
    assert any("accent-to-base step" in reason for reason in abrupt["reasons"])


# -- family axes: one material, one axis -------------------------------------


def _tinted(path: Path, colour: tuple[int, int, int], offset: int = 0) -> Path:
    image = Image.new("RGBA", (16, 16), colour + (255,))
    for y in range(16):
        for x in range(16):
            if (x + y) % 5 == 0:
                image.putpixel((x, y), tuple(min(255, value + 10 + offset) for value in colour) + (255,))
    image.save(path)
    return path


def test_a_family_of_three_grey_blues_is_consistent_and_an_orange_member_is_not(tmp_path: Path) -> None:
    stone = _tinted(tmp_path / "stone.png", (62, 68, 80))
    deepslate = _tinted(tmp_path / "deepslate.png", (48, 52, 62), offset=2)
    ore = _tinted(tmp_path / "ore.png", (56, 62, 74), offset=4)
    coherent = style.family_axes([stone, deepslate, ore])
    assert coherent["consistent"] is True, coherent
    assert coherent["hue_span_degrees"] is not None and coherent["hue_span_degrees"] < 26.0

    warm = _tinted(tmp_path / "warm.png", (168, 96, 40))
    broken = style.family_axes([stone, deepslate, warm])
    assert broken["consistent"] is False, broken
    assert any("hue span" in reason for reason in broken["reasons"])


def test_an_accent_does_not_count_as_a_family_hue(tmp_path: Path) -> None:
    stone = _tinted(tmp_path / "stone.png", (62, 68, 80))
    plain_ingot = _tinted(tmp_path / "ingot.png", (62, 68, 80), offset=2)
    accent_ingot = tmp_path / "ingot_accent.png"
    image = Image.new("RGBA", (16, 16), (62, 68, 80, 255))
    for y in range(16):
        for x in range(16):
            if (x + y) % 5 == 0:
                image.putpixel((x, y), (72, 78, 90, 255))
    for x in range(4, 10):
        image.putpixel((x, 2), (242, 192, 112, 255))
    image.save(accent_ingot)

    with_accent = style.family_axes(
        [stone, accent_ingot], accent_colors=["#F2C070"], base_colors=["#3E4450"]
    )
    assert with_accent["consistent"] is True, with_accent
    member = next(
        row for row in with_accent["members"]
        if Path(str(row["sprite"])).name == "ingot_accent.png"
    )
    assert member["accent_pixels"] == 6
    assert member["hue_degrees"] is not None
    assert plain_ingot.exists()


# -- wired into the renderer and the plan ------------------------------------


def test_shade_mode_gradient_really_yields_a_smoother_ramp_than_bands(tmp_path: Path) -> None:
    palette = {
        "deep": "#2A2D33",
        "mid": "#3A3E46",
        "light": "#4C525C",
    }
    banded = AppearanceSpec(
        palette=palette,
        parts={"face": PartAppearance(
            colors=["deep", "mid", "light"], material="stone",
            shade_axis="top", shade_mode="bands",
        )},
    )
    smooth = AppearanceSpec(
        palette=palette,
        parts={"face": PartAppearance(
            colors=["deep", "mid", "light"], material="stone",
            shade_axis="top", shade_mode="gradient",
        )},
    )
    banded_out = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, banded), tmp_path / "banded", package=False
    )
    smooth_out = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, smooth), tmp_path / "smooth", package=False
    )
    banded_bands = style.band_report(banded_out.sprite_path)
    smooth_bands = style.band_report(smooth_out.sprite_path)
    assert banded_bands["band_count"] <= 4, banded_bands["bands"]
    assert smooth_bands["band_count"] > banded_bands["band_count"], (
        smooth_bands["bands"], banded_bands["bands"]
    )


def test_a_declared_budget_of_zero_fails_the_render_when_the_plan_spends_one(tmp_path: Path) -> None:
    rows = [list("." * 16) for _ in range(16)]
    rows[8][8] = "1"
    accent_plan = AppearanceSpec(
        palette={"base": "#3C424E", "star": "#F2C070"},
        parts={"face": PartAppearance(colors=["base"], material="stone", shade_axis="none")},
        accent_colors=["#F2C070"],
        accent_budget=0,
        pixel_map={"legend": {"1": "star"}, "rows": ["".join(row) for row in rows]},
    )
    result = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, accent_plan), tmp_path / "budget", package=False
    )
    assert result.validation.passed is False
    assert result.validation.metrics["style.accent_pixels"] == 1
    assert any("exceed the declared budget of 0" in error for error in result.validation.errors)


def test_the_same_one_pixel_accent_passes_when_no_budget_is_declared(tmp_path: Path) -> None:
    rows = [list("." * 16) for _ in range(16)]
    rows[8][8] = "1"
    accent_plan = AppearanceSpec(
        palette={"base": "#3C424E", "star": "#F2C070"},
        parts={"face": PartAppearance(colors=["base"], material="stone", shade_axis="none")},
        accent_colors=["#F2C070"],
        pixel_map={"legend": {"1": "star"}, "rows": ["".join(row) for row in rows]},
    )
    result = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, accent_plan), tmp_path / "unchecked", package=False
    )
    assert result.validation.passed is True
    assert result.validation.metrics["style.accent_pixels"] == 1
    assert (tmp_path / "unchecked" / "style_report.json").exists()


def test_a_declared_minimum_cluster_gates_the_render_and_cleanup_repairs_it(tmp_path: Path) -> None:
    rows = [list("." * 16) for _ in range(16)]
    rows[2][2] = "1"
    for x in range(8, 12):
        for y in range(9, 11):
            rows[y][x] = "1"

    def _plan(*, cleanup: bool) -> AppearanceSpec:
        return AppearanceSpec(
            palette={"base": "#3C424E", "star": "#F2C070"},
            parts={"face": PartAppearance(colors=["base"], material="stone", shade_axis="none")},
            accent_colors=["#F2C070"],
            accent_budget=0,
            accent_min_cluster=4,
            accent_cleanup=cleanup,
            pixel_map={"legend": {"1": "star"}, "rows": ["".join(row) for row in rows]},
        )

    # The gate first: with no repair declared the speck survives and is reported.
    gated = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, _plan(cleanup=False)), tmp_path / "gated", package=False
    )
    assert gated.validation.passed is False
    assert gated.validation.metrics["style.accent_below_minimum_pixels"] == 1
    gated_sprite = Image.open(gated.sprite_path).convert("RGBA")
    assert gated_sprite.getpixel((2, 2))[:3] == (242, 192, 112), "the speck is still there"

    # Then the repair the author opted into, on the same fault.
    repaired = GenerationPipeline(critic=None, max_geometry_repairs=0).run(
        _block_plan(tmp_path, _plan(cleanup=True)), tmp_path / "repaired", package=False
    )
    assert repaired.sprite_path is not None
    sprite = Image.open(repaired.sprite_path).convert("RGBA")
    assert sprite.getpixel((2, 2))[:3] != (242, 192, 112), "the lone speck was repainted"
    assert sprite.getpixel((9, 9))[:3] == (242, 192, 112), "the deposit survives"
    # Eight deposited pixels remain, which is still over a zero budget: the
    # renderer removes noise, it does not silently rewrite the author's cap.
    assert repaired.validation.metrics["style.accent_pixels"] == 8
    assert repaired.validation.metrics["style.accent_below_minimum_pixels"] == 0
    assert repaired.validation.passed is False, "the budget of 0 is still breached by the deposit"


def test_one_accent_hue_across_the_family_passes_and_two_fail(tmp_path: Path) -> None:
    """The "can you even tell these were one object" gate.

    Both sprites here share a base hue, so a base-only axis test cannot see the
    problem: the amber seam on one and the cyan seam on the other are what make
    them read as unrelated objects.
    """

    def _with_accent(path: Path, accent: tuple[int, int, int]) -> Path:
        image = Image.new("RGBA", (16, 16), (62, 68, 80, 255))
        for x in range(4, 10):
            image.putpixel((x, 6), accent + (255,))
        image.save(path)
        return path

    amber = _with_accent(tmp_path / "amber.png", (240, 190, 110))
    cyan = _with_accent(tmp_path / "cyan.png", (110, 210, 235))
    shared = style.family_axes(
        [amber, _with_accent(tmp_path / "amber2.png", (243, 195, 116))],
        accent_colors=["#F0BE6E"],
    )
    assert shared["consistent"] is True, shared
    assert shared["accent_members"] == 2
    assert shared["accent_hue_span_degrees"] < 14.0

    split = style.family_axes([amber, cyan], accent_colors=["#F0BE6E", "#6ED2EB"])
    assert split["consistent"] is False, split
    assert split["accent_hue_span_degrees"] > 14.0
    assert any("accent hue span" in reason for reason in split["reasons"])
    # ...while the base material axis alone still looks perfectly consistent.
    assert split["hue_span_degrees"] < 26.0


def test_a_family_with_no_accents_reports_no_accent_axis(tmp_path: Path) -> None:
    stone = _tinted(tmp_path / "stone.png", (62, 68, 80))
    other = _tinted(tmp_path / "other.png", (58, 64, 76), offset=2)
    report = style.family_axes([stone, other])
    assert report["accent_hue_span_degrees"] is None
    assert report["accent_members"] == 0
    assert report["consistent"] is True


def test_validate_style_reports_the_numbers_even_with_nothing_declared(tmp_path: Path) -> None:
    plain = _solid(tmp_path / "plain.png", (60, 66, 78, 255))
    appearance = AppearanceSpec(
        palette={"base": "#3C424E"},
        parts={"face": PartAppearance(colors=["base"])},
    )
    result = validate_style(appearance, Image.open(plain))
    assert result.passed is True
    assert result.metrics["accent_pixels"] == 0
    assert result.metrics["accent_budget"] == -1
    assert result.metrics["band_verdict"] == "unchecked"


@pytest.mark.parametrize("width,height", [(16, 16), (32, 32)])
def test_band_report_scales_beyond_a_single_block(tmp_path: Path, width: int, height: int) -> None:
    image = Image.new("RGBA", (width, height), (40, 44, 52, 255))
    image.save(tmp_path / "flat.png")
    report = style.band_report(tmp_path / "flat.png")
    assert report["band_count"] == 1
    assert report["largest_flat_share"] == 1.0
    assert report["isolated_pixels"] == 0
