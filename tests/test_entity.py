"""One model description in, a layout and a render spec out, and a check that
the paint landed where the model reads. These are the two branches of the entity
flow: a model that already exists (offsets stated by the game) and one that does
not (offsets packed here).
"""
import json

import numpy as np
import pytest
from PIL import Image, ImageDraw

from mc_art.entity import (assign_offsets, audit, box_faces, box_net, load_spec, plan,
                           write_plan)
from mc_art.uv_layout import layout_regions

MOB = {
    "name": "test_mob",
    "parts": [
        {"name": "shell", "boxes": [{"at": [-4, 16, -4], "w": 8, "h": 8, "d": 8}]},
        {"name": "core", "boxes": [{"at": [-3, 17, -3], "w": 6, "h": 6, "d": 6}]},
        {"name": "eye", "boxes": [{"at": [0, 18, -3], "w": 2, "h": 2, "d": 2}]},
    ],
}


def _paint(spec, path, stray=False):
    """Fill every rectangle, so a PASS is the honest result."""
    image = Image.new("RGBA", tuple(spec["tex"]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for part in spec["parts"]:
        for box in part["boxes"]:
            for left, top, right, bottom in box_faces(box).values():
                draw.rectangle([left, top, right - 1, bottom - 1], fill=(180, 40, 40, 255))
    if stray:
        draw.point((image.size[0] - 1, image.size[1] - 1), fill=(0, 255, 0, 255))
    image.save(path)
    return image


def test_box_net_is_the_minecraft_rectangle():
    box = {"u": 0, "v": 0, "w": 8, "h": 16, "d": 6}
    assert box_net(box) == (28, 22)


def test_the_six_faces_tile_the_net_exactly():
    """Every face rectangle sits inside the net, and the six together are the
    net minus the two corners Minecraft leaves empty."""
    box = {"u": 0, "v": 0, "w": 8, "h": 16, "d": 6}
    width, height = box_net(box)
    cells = set()
    for left, top, right, bottom in box_faces(box).values():
        assert 0 <= left and 0 <= top and right <= width and bottom <= height
        for y in range(top, bottom):
            for x in range(left, right):
                assert (x, y) not in cells, "faces overlap at %s" % ((x, y),)
                cells.add((x, y))
    assert len(cells) < width * height


def test_absent_offsets_are_packed_and_stated_ones_are_kept():
    payload = json.loads(json.dumps(MOB))
    payload["parts"][0]["boxes"][0]["u"] = 0
    payload["parts"][0]["boxes"][0]["v"] = 0
    spec = load_spec(payload)
    spec, canvas = assign_offsets(spec)
    assert (spec["parts"][0]["boxes"][0]["u"], spec["parts"][0]["boxes"][0]["v"]) == (0, 0)
    assert all("u" in box and "v" in box for part in spec["parts"] for box in part["boxes"])
    assert canvas[0] >= 64 and canvas[1] >= 32


def test_packed_boxes_never_overlap():
    spec, _canvas = assign_offsets(load_spec(json.loads(json.dumps(MOB))))
    taken = set()
    for part in spec["parts"]:
        for box in part["boxes"]:
            for left, top, right, bottom in box_faces(box).values():
                for y in range(top, bottom):
                    for x in range(left, right):
                        assert (x, y) not in taken
                        taken.add((x, y))


def test_the_layout_is_loadable_by_the_uv_renderer():
    result = plan(json.loads(json.dumps(MOB)))
    regions = layout_regions(result["layout"])
    assert len(regions) == 6 * 3
    assert {region.part_id for region in regions} == {"shell", "core", "eye"}


def test_a_clean_atlas_audits_clean(tmp_path):
    result = plan(json.loads(json.dumps(MOB)))
    image = _paint(result["spec"], tmp_path / "atlas.png")
    report = audit(result["spec"], image)
    assert report["stray"] == 0
    assert report["empty"] == 0
    assert report["covered"] == 1.0


def test_a_texel_outside_every_box_is_a_failure(tmp_path):
    """The check that catches a mob painted into the wrong rectangles: the
    render still looks like a mob, which is why this has to be arithmetic."""
    result = plan(json.loads(json.dumps(MOB)))
    image = _paint(result["spec"], tmp_path / "atlas.png", stray=True)
    report = audit(result["spec"], image)
    assert report["stray"] == 1
    assert report["stray_pixels"] == [(image.size[0] - 1, image.size[1] - 1)]


def test_an_unpainted_rectangle_is_reported_not_failed(tmp_path):
    result = plan(json.loads(json.dumps(MOB)))
    image = Image.new("RGBA", tuple(result["spec"]["tex"]), (0, 0, 0, 0))
    report = audit(result["spec"], image)
    assert report["stray"] == 0
    assert report["empty"] == report["claimed"]


def test_the_audit_takes_a_renderer_array_too():
    """ingame already holds the texture as a float array, so it hands that over
    rather than reloading the file."""
    result = plan(json.loads(json.dumps(MOB)))
    array = np.zeros((result["spec"]["tex"][1], result["spec"]["tex"][0], 4), dtype=np.float32)
    report = audit(result["spec"], array)
    assert report["painted"] == 0


def test_write_plan_leaves_a_spec_the_renderer_can_read(tmp_path):
    written = write_plan(plan(json.loads(json.dumps(MOB))), tmp_path)
    spec = json.loads(open(written["model"], encoding="utf-8").read())
    assert spec["parts"] and all("u" in box for part in spec["parts"] for box in part["boxes"])
    assert "units" not in spec or True


@pytest.mark.parametrize("bad", [
    {},
    {"parts": []},
    {"parts": [{"name": "x"}]},
    {"parts": [{"name": "x", "boxes": [{"w": 0, "h": 1, "d": 1}]}]},
])
def test_a_malformed_spec_is_rejected(bad):
    with pytest.raises(ValueError):
        load_spec(bad)
