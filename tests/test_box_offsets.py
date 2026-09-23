"""Box specs may state the texture offsets an existing atlas already uses.

The shelf packer invents a layout. It cannot describe one: reproducing the
vanilla sheep atlas means putting the head net at (0,0), the body at (28,8) and
the shared leg net at (0,16), and those are the model's numbers, not a packing
choice. Without this the engine could author a new atlas but never check itself
against a real one.
"""

from __future__ import annotations

from pathlib import Path

from mc_art.box_model import pack_boxes

SHEEP = [
    {"id": "head", "part_id": "head", "size": [6, 6, 8], "u": 0, "v": 0},
    {"id": "body", "part_id": "body", "size": [8, 16, 6], "u": 28, "v": 8},
    {"id": "leg1", "part_id": "legs", "size": [4, 12, 4], "u": 0, "v": 16},
    {"id": "leg2", "part_id": "legs", "size": [4, 12, 4], "u": 0, "v": 16},
]


def _layout(boxes):
    from mc_art.box_model import boxes_from_dict

    return pack_boxes(boxes_from_dict({"boxes": boxes}))


def test_stated_offsets_are_kept_verbatim() -> None:
    layout = _layout(SHEEP)
    placed = {(cube.id, cube.u, cube.v) for cube in layout.cubes}
    assert ("head", 0, 0) in placed
    assert ("body", 28, 8) in placed
    assert ("leg1", 0, 16) in placed


def test_a_stated_layout_derives_the_canvas_from_its_regions() -> None:
    """The sheep atlas is exactly 64x32, and that falls out of the offsets."""
    layout = _layout(SHEEP)
    assert list(layout.canvas) == [64, 32]


def test_two_boxes_may_share_one_net() -> None:
    """Four legs share the (0,16) net in the vanilla model."""
    layout = _layout(SHEEP)
    legs = [cube for cube in layout.cubes if cube.part_id == "legs"]
    assert len(legs) == 2
    assert {(cube.u, cube.v) for cube in legs} == {(0, 16)}


def test_mixing_stated_and_unstated_offsets_is_rejected() -> None:
    mixed = [dict(SHEEP[0]), {"id": "loose", "part_id": "loose", "size": [4, 4, 4]}]
    try:
        _layout(mixed)
    except ValueError as exc:
        assert "every box" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a half-stated layout is ambiguous and must be refused")


def test_offsets_that_overflow_a_forced_canvas_are_rejected() -> None:
    """The canvas is derived from the offsets, so only a forced width can be too small."""
    from mc_art.box_model import boxes_from_dict

    boxes = boxes_from_dict({"boxes": [SHEEP[1]]})   # a 28-wide net at u=28
    try:
        pack_boxes(boxes, canvas_width=32)
    except ValueError as exc:
        assert "do not fit" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a 28-wide net at u=28 cannot fit a 32-wide canvas")


def test_the_wrong_head_box_shows_up_as_short_regions() -> None:
    """The wool overlay's head is 6x6x6; the body layer's is 6x6x8.

    Comparing an inferred model against the real atlas is how that was found.
    A box four columns too deep does not leave a region empty -- it leaves
    several partly covered, which is what a coverage check is for.
    """
    import json

    from PIL import Image

    from mc_art.box_model import boxes_from_dict
    from mc_art.planfile import uv_layout_from_file

    # A synthetic wool head: exactly the 6x6x6 net, 24x12.
    atlas = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    for y in range(0, 12):
        for x in range(0, 24):
            atlas.putpixel((x, y), (240, 240, 240, 255))
    atlas.save("/tmp/wool_head.png", "PNG")

    def coverage(box):
        layout = pack_boxes(boxes_from_dict({"boxes": [box]}), canvas_width=64)
        path = "/tmp/wool_layout.json"
        Path(path).write_text(json.dumps(layout.to_dict()), encoding="utf-8")
        worst = 1.0
        with Image.open("/tmp/wool_head.png") as loaded:
            wool = loaded.convert("RGBA")
        for region in uv_layout_from_file(path):
            left, top, right, bottom = region.bbox
            cells = [(x, y) for y in range(top, bottom) for x in range(left, right)]
            filled = sum(1 for x, y in cells if wool.getpixel((x, y))[3] >= 8)
            worst = min(worst, filled / float(len(cells)))
        return worst

    body_layer = {"id": "head", "part_id": "head", "size": [6, 6, 8], "u": 0, "v": 0}
    wool_layer = {"id": "head", "part_id": "head", "size": [6, 6, 6], "u": 0, "v": 0}
    assert coverage(wool_layer) == 1.0, "the corrected head box fills every region"
    assert coverage(body_layer) < 0.8, "the body layer's deeper head box does not"
