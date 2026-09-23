"""Entity UV: find a shape, author one, and see what it produced.

A live entity run (a blood slime) needed a 16-cube box unwrap and had to
hand-write it, because the engine shipped box-decomposition support and three
preview renderers with no way in. These are the way in.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from mc_art.uv_tools import annotate_layout, layout_from_boxes, list_layouts, render_views


def test_the_shipped_layouts_are_discoverable() -> None:
    """Finding the corresponding shape beats inventing one."""
    found = list_layouts()
    names = {entry["name"] for entry in found}
    assert "vanilla_1_12_cow_64" in names
    assert "vanilla_1_12_armor_layer_1_64" in names
    cow = next(entry for entry in found if entry["name"] == "vanilla_1_12_cow_64")
    assert cow["canvas"] == [64, 32]
    assert {"head", "body", "legs"} <= set(cow["parts"])
    assert "top" in cow["faces_per_part"]["head"]


def test_a_sixteen_cube_derives_the_standard_slime_atlas(tmp_path: Path) -> None:
    """No vanilla slime layout ships, so the caller states the box."""
    spec = {"boxes": [{"id": "shell", "part_id": "shell", "size": [16, 16, 16]}]}
    result = layout_from_boxes(spec, tmp_path / "slime.json")
    assert result["canvas"] == [64, 32], "a 16-cube unwraps to the canonical 64x32"
    assert result["regions"] == 6
    written = json.loads((tmp_path / "slime.json").read_text(encoding="utf-8"))
    assert written["source"] == "model-authored box decomposition"
    assert len(written["cubes"]) == 1


def test_several_boxes_share_one_atlas_without_overlapping(tmp_path: Path) -> None:
    spec = {"boxes": [
        {"id": "body", "part_id": "body", "size": [8, 8, 6]},
        {"id": "head", "part_id": "head", "size": [6, 6, 6]},
    ]}
    result = layout_from_boxes(spec, tmp_path / "two.json")
    assert result["parts"] == ["body", "head"]
    written = json.loads((tmp_path / "two.json").read_text(encoding="utf-8"))
    boxes = [(c["u"], c["v"], c["u"] + 2 * c["depth"] + 2 * c["width"], c["v"] + c["depth"] + c["height"])
             for c in written["cubes"]]
    first, second = boxes
    overlaps = not (first[2] <= second[0] or second[2] <= first[0]
                    or first[3] <= second[1] or second[3] <= first[1])
    assert not overlaps, "two boxes must not claim the same atlas cells"


def test_an_impossible_box_is_rejected_with_a_reason() -> None:
    try:
        layout_from_boxes({"boxes": [{"id": "x", "size": [0, 4, 4]}]}, "/tmp/unused.json")
    except ValueError as exc:
        assert "width" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a zero-width box must be rejected")


def test_the_annotated_map_names_every_region(tmp_path: Path) -> None:
    """Without this the atlas is mostly empty canvas and guesswork."""
    atlas = tmp_path / "atlas.png"
    Image.new("RGBA", (64, 32), (90, 60, 40, 255)).save(atlas, "PNG")
    layout = tmp_path / "cow.json"
    layout.write_text(
        (Path(__file__).resolve().parents[1] / "layouts" / "vanilla_1_12_cow_64.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    from mc_art.planfile import uv_layout_from_file

    image = annotate_layout(atlas, uv_layout_from_file(layout), scale=4)
    assert image.width >= 64 * 4
    assert image.height > 32 * 4, "the legend lives under the atlas"

    result = render_views(layout, atlas, tmp_path / "out", scale=4)
    assert Path(result["views"]["uvmap"]).exists()
    assert Path(result["views"]["front"]).exists(), "a preview the caller can actually look at"


def test_a_layout_without_a_top_face_still_yields_the_map(tmp_path: Path) -> None:
    """The previews need specific faces; the map must not depend on them."""
    atlas = tmp_path / "flat.png"
    Image.new("RGBA", (16, 16), (200, 60, 60, 255)).save(atlas, "PNG")
    layout = tmp_path / "flat.json"
    layout.write_text(json.dumps({
        "format": "mc-art-uv-layout/1",
        "texture_width": 16, "texture_height": 16,
        "regions": [{"id": "surface", "part_id": "surface", "bbox": [0, 0, 16, 16], "face": "custom"}],
    }), encoding="utf-8")
    result = render_views(layout, atlas, tmp_path / "out", scale=4)
    assert Path(result["views"]["uvmap"]).exists()
