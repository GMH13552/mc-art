"""Tests for the reference-selection diagnosis.

"深渊原石参考的是浅层原石" is not a question about a palette; it is a question
about which file was read. So the diagnosis has to be the renderer's own
decision table, not a second opinion computed beside it -- these tests pin that
down by comparing the report's chosen reference with what the renderer loads.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from mc_art.appearance import _select_reference, reference_selection_report
from mc_art.contracts import ReferenceAsset, ReferenceRole


def _shape(path: Path, boxes: list[list[int]], colour=(90, 70, 45, 255)) -> Path:
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for box in boxes:
        draw.rectangle(box, fill=colour)
    image.save(path)
    return path


def _solid(path: Path, colour) -> Path:
    Image.new("RGBA", (16, 16), colour).save(path)
    return path


def test_the_report_names_the_reference_the_renderer_actually_loads(tmp_path: Path) -> None:
    mask = Image.new("L", (16, 16), 0)
    ImageDraw.Draw(mask).rectangle([0, 0, 15, 15], fill=255)
    matching = ReferenceAsset(
        path=str(_solid(tmp_path / "deepslate.png", (70, 74, 84, 255))),
        name="deepslate",
        roles=[ReferenceRole.SHAPE, ReferenceRole.MATERIAL],
    )
    other = ReferenceAsset(
        path=str(_solid(tmp_path / "stone.png", (120, 118, 110, 255))),
        name="stone",
        roles=[ReferenceRole.SHAPE],
    )
    report = reference_selection_report([matching, other], 16, 16, target_mask=mask)
    chosen = _select_reference([matching, other], 16, 16, target_mask=mask)
    assert chosen is not None
    assert report["chosen"]["path"] == chosen[1].path == matching.path
    assert "highest score" in report["reason"]
    names = [row["name"] for row in report["candidates"]]
    assert names == ["deepslate", "stone"] or set(names) == {"deepslate", "stone"}


def test_a_wrong_sized_candidate_is_reported_with_the_rule_that_excluded_it(tmp_path: Path) -> None:
    reference = ReferenceAsset(
        path=str(_solid(tmp_path / "wide_atlas.png", (70, 74, 84, 255))),
        name="wide_atlas",
        roles=[ReferenceRole.MATERIAL, ReferenceRole.PIXEL_STYLE],
    )
    Image.new("RGBA", (64, 16), (70, 74, 84, 255)).save(tmp_path / "wide_atlas.png")
    report = reference_selection_report([reference], 16, 16)
    assert report["chosen"] is None
    row = report["candidates"][0]
    assert row["eligible"] is False
    assert row["reason_code"] == "size-mismatch"
    assert "neither the 16x16 canvas nor a horizontal face strip" in row["reason"]
    assert report["step"] == "no-candidate-fit"
    assert report["counts"] == {"offered": 1, "readable": 1, "eligible": 0}


def test_the_step_names_the_break_when_no_reference_was_offered_at_all(tmp_path: Path) -> None:
    report = reference_selection_report([], 16, 16)
    assert report["chosen"] is None
    assert report["step"] == "no-references-offered"
    assert report["counts"] == {"offered": 0, "readable": 0, "eligible": 0}
    assert "reference directory" in report["step_detail"]


def test_the_step_names_the_break_when_every_candidate_is_unreadable(tmp_path: Path) -> None:
    missing = ReferenceAsset(
        path=str(tmp_path / "gone.png"),
        name="gone",
        roles=[ReferenceRole.MATERIAL],
    )
    report = reference_selection_report([missing], 16, 16)
    assert report["step"] == "no-candidate-readable"
    assert report["counts"] == {"offered": 1, "readable": 0, "eligible": 0}
    assert "gone.png" in report["step_detail"]


def test_an_unreadable_candidate_is_reported_rather_than_silently_dropped(tmp_path: Path) -> None:
    missing = ReferenceAsset(
        path=str(tmp_path / "not_there.png"),
        name="not_there",
        roles=[ReferenceRole.MATERIAL],
    )
    usable = ReferenceAsset(
        path=str(_solid(tmp_path / "usable.png", (60, 64, 76, 255))),
        name="usable",
        roles=[ReferenceRole.MATERIAL],
    )
    report = reference_selection_report([missing, usable], 16, 16)
    assert report["chosen"]["name"] == "usable"
    rows = {row["name"]: row for row in report["candidates"]}
    assert rows["not_there"]["eligible"] is False
    assert rows["not_there"]["reason_code"] == "unreadable"
    assert rows["not_there"]["reason"].startswith("the image could not be read from")
    assert rows["not_there"]["reason"].endswith("not_there.png")


def test_a_negative_reference_is_named_as_the_reason_it_is_not_used(tmp_path: Path) -> None:
    blocked = ReferenceAsset(
        path=str(_solid(tmp_path / "bright_stone.png", (200, 200, 200, 255))),
        name="bright_stone",
        roles=[ReferenceRole.NEGATIVE],
    )
    report = reference_selection_report([blocked], 16, 16)
    row = report["candidates"][0]
    assert row["eligible"] is False
    assert "declared negative" in row["reason"]
    assert report["chosen"] is None


def test_role_weight_breaks_a_tie_between_two_identical_canvases(tmp_path: Path) -> None:
    first = _solid(tmp_path / "first.png", (60, 64, 76, 255))
    second = _solid(tmp_path / "second.png", (60, 64, 76, 255))
    weak = ReferenceAsset(path=str(first), name="weak", roles=[ReferenceRole.MATERIAL])
    strong = ReferenceAsset(
        path=str(second), name="strong",
        roles=[ReferenceRole.SHAPE, ReferenceRole.PIXEL_STYLE],
    )
    report = reference_selection_report([weak, strong], 16, 16)
    assert report["chosen"]["name"] == "strong"
    assert report["chosen"]["role_score"] == 5
    assert report["chosen"]["alpha_overlap"] is None


def test_a_smaller_face_strip_is_eligible_and_reported_as_face_tile(tmp_path: Path) -> None:
    tile = _solid(tmp_path / "planks.png", (120, 84, 48, 255))
    Image.new("RGBA", (16, 16), (120, 84, 48, 255)).save(tile)
    reference = ReferenceAsset(
        path=str(tile), name="planks",
        roles=[ReferenceRole.MATERIAL, ReferenceRole.PIXEL_STYLE],
    )
    mask = Image.new("L", (64, 16), 255)
    report = reference_selection_report([reference], 64, 16, target_mask=mask)
    assert report["chosen"]["mode"] == "face_tile"
    assert report["chosen"]["name"] == "planks"
