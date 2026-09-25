"""The in-game view is the last check before a delivery, so the renderer itself
has to be checkable. These need no jar: the glyph control is generated, and the
one test that matters most injects the bug this renderer was written to catch.
"""
from pathlib import Path

import numpy as np

import mc_art.ingame as ing
from mc_art.ingame import (CONTROLS, TextureSource, control_model, orientation_check,
                           selftest, sheep_skin, sheep_wool)


def test_orientation_check_passes():
    ok, detail = orientation_check()
    assert ok, detail


def test_orientation_check_catches_the_corner_order_bug():
    """The bug that smeared the first cow: reading the quad as top-left then
    clockwise instead of TexturedQuad's (u2,v1) (u1,v1) (u1,v2) (u2,v2). It
    rotates every face 180 degrees and mirrors it, and the result still looks
    plausibly like an animal -- which is exactly why this is an assertion."""
    def broken(model, pose=None):
        tex_w, tex_h = model.get("tex", (64, 32))
        for part in model["parts"]:
            pivot = part["pivot"]
            rotation = dict(part.get("rot", {}), **((pose or {}).get(part["name"], {})))
            for box in part["boxes"]:
                ox, oy, oz = box["at"]
                w, h, d = box["w"], box["h"], box["d"]
                inflate = box.get("inflate", 0.0)
                table = ing.corners(ox - inflate, oy - inflate, oz - inflate,
                                    ox + w + inflate, oy + h + inflate, oz + d + inflate)
                for name, keys, rect_of in ing.QUADS:
                    u1, v1, u2, v2 = rect_of(box["u"], box["v"], w, h, d)
                    corners = [ing.place(table[key], pivot, rotation) for key in keys]
                    world = [ing.to_world(point) for point in corners]
                    pairs = ((u1, v1), (u2, v1), (u2, v2), (u1, v2))
                    uvs = [(pair[0] / tex_w, pair[1] / tex_h) for pair in pairs]
                    yield name, world, uvs, ing.to_world((0, 0, 1))

    original = ing.iter_quads
    ing.iter_quads = broken
    try:
        ok, detail = orientation_check()
    finally:
        ing.iter_quads = original
    assert not ok
    assert "top-left" in detail


def test_selftest_writes_the_glyph_and_passes(tmp_path):
    ok, detail, files = selftest(None, tmp_path)
    assert ok, detail
    assert len(files) == 2
    assert all(Path(name).is_file() for name in files)


def test_the_glyph_renders_upright(tmp_path):
    """The picture half of the self test: with the glyph in the head's front
    rect, the rendered front view must carry more black in its upper half than
    its lower half -- a 180 degree flip or a mirror breaks that."""
    from PIL import Image

    ok, _, files = selftest(None, tmp_path)
    assert ok
    front = next(name for name in files if name.endswith("selftest_front.png"))
    pixels = np.asarray(Image.open(front).convert("L"))
    dark = pixels < 64
    rows = np.where(dark.any(axis=1))[0]
    assert len(rows) > 4
    top = dark[rows.min():rows.min() + (rows.max() - rows.min()) // 2, :].sum()
    bottom = dark[rows.min() + (rows.max() - rows.min()) // 2:rows.max() + 1, :].sum()
    assert top > bottom


def test_every_control_has_boxes_and_a_texture():
    for name in CONTROLS:
        model = control_model(name)
        boxes = [box for part in model["parts"] for box in part["boxes"]]
        assert boxes, name
        assert model["texture"].startswith("textures/"), name


def test_a_quadruped_torso_is_rotated_and_its_legs_are_not():
    """ModelQuadruped sets body.rotateAngleX to a right angle. Forgetting it
    lays the torso out as a slab, which is what left the cow flat."""
    for name in ("cow", "sheep", "wool"):
        parts = {part["name"]: part for part in control_model(name)["parts"]}
        assert parts["body"]["rot"]["x"] == 90.0, name
        assert parts["head"].get("rot", {}) == {}, name
        assert parts["leg1"].get("rot", {}) == {}, name


def test_the_wool_layer_is_not_the_skin_inflated():
    skin = {part["name"]: part["boxes"][0] for part in sheep_skin()["parts"]}
    wool = {part["name"]: part["boxes"][0] for part in sheep_wool()["parts"]}
    assert (skin["head"]["w"], skin["head"]["h"], skin["head"]["d"]) == (6, 6, 8)
    assert (wool["head"]["w"], wool["head"]["h"], wool["head"]["d"]) == (6, 6, 6)
    assert skin["leg1"]["h"] == 12
    assert wool["leg1"]["h"] == 6
    assert (wool["head"]["inflate"], wool["body"]["inflate"], wool["leg1"]["inflate"]) == (0.6, 1.75, 0.5)


def test_texture_source_finds_both_the_1_12_and_the_1_13_layouts(tmp_path):
    root = tmp_path / "assets" / "minecraft"
    (root / "textures" / "blocks").mkdir(parents=True)
    (root / "textures" / "block").mkdir(parents=True)
    (root / "textures" / "blocks" / "ancient.png").write_bytes(b"old")
    (root / "textures" / "block" / "modern.png").write_bytes(b"new")
    with TextureSource(tmp_path) as source:
        assert source.read("textures/blocks/ancient.png") == b"old"
        assert source.read("textures/block/modern.png") == b"new"
        assert source.read("textures/block/missing.png") is None
