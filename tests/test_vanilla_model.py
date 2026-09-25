"""The entity model is bytecode, so the layout has to be read, not remembered.

These fixtures are plain javap dumps of the 1.12.2 models, so the tests run with
no jar and no JVM: they pin the reader, and they pin the shipped sheep layouts
against it. The wool-head net is the regression that motivated them -- assuming
the wool overlay was the skin model inflated drew the fur four pixels too wide
and its legs six pixels too tall.
"""
import json
from pathlib import Path

import numpy as np

from mc_art.vanilla_model import (boxes_for_class, layout_document, render_spec,
                                   transform_fields)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _source(*fixtures):
    # fixture files are named for the animal, but the bytecode refers to the
    # model by its obfuscated class name
    texts = {name.split("_", 1)[1]: (FIXTURES / ("javap_%s.txt" % name)).read_text(encoding="utf-8")
             for name in fixtures}

    def lookup(cls):
        if cls not in texts:
            raise KeyError(cls)
        return texts[cls]

    return lookup


SHEEP = ("sheep_bqp", "sheep_bqo", "sheep_bqm", "renderer_brs")
COW = ("cow_bpn", "sheep_bqm", "renderer_brs")


def test_skin_boxes_come_out_of_the_constructor():
    boxes = boxes_for_class(_source(*SHEEP), "bqp")
    assert [(b.part, b.u, b.v, b.w, b.h, b.d) for b in boxes] == [
        ("a", 0, 0, 6, 6, 8),
        ("b", 28, 8, 8, 16, 6),
        ("c", 0, 16, 4, 12, 4),
        ("d", 0, 16, 4, 12, 4),
        ("e", 0, 16, 4, 12, 4),
        ("f", 0, 16, 4, 12, 4),
    ]


def test_legs_are_inherited_from_the_super_constructor():
    # ModelSheep1 never mentions the legs; ModelQuadruped(12, 0.0F) supplies
    # 4 x par1 x 4, and par1 has to be threaded through as 12.
    parts = {b.part: b for b in boxes_for_class(_source(*SHEEP), "bqp")}
    assert (parts["c"].w, parts["c"].h, parts["c"].d) == (4, 12, 4)


def test_wool_is_its_own_model_with_its_own_boxes():
    skin = {b.part: b for b in boxes_for_class(_source(*SHEEP), "bqp")}
    wool = {b.part: b for b in boxes_for_class(_source(*SHEEP), "bqo")}
    assert skin["a"].net_size == (28, 14)
    assert wool["a"].net_size == (24, 12)
    assert skin["c"].net_size == (16, 16)
    assert wool["c"].net_size == (16, 10)
    # inflation grows the rendered cube and never the UV net
    assert (wool["a"].delta, wool["b"].delta, wool["c"].delta) == (0.6, 1.75, 0.5)
    assert (skin["a"].delta, skin["b"].delta) == (0.0, 0.0)


def test_a_renderer_can_hold_several_boxes_at_different_offsets():
    # ModelCow attaches two horns to the head renderer through the fluent
    # setTextureOffset, and its udder to the body renderer.
    boxes = boxes_for_class(_source(*COW), "bpn")
    assert [(b.part, b.u, b.v, b.w, b.h, b.d) for b in boxes] == [
        ("a", 0, 0, 8, 8, 6),
        ("a", 22, 0, 1, 3, 1),
        ("a", 22, 0, 1, 3, 1),
        ("b", 18, 4, 12, 18, 10),
        ("b", 52, 0, 4, 6, 1),
        ("c", 0, 16, 4, 12, 4),
        ("d", 0, 16, 4, 12, 4),
        ("e", 0, 16, 4, 12, 4),
        ("f", 0, 16, 4, 12, 4),
    ]


def test_shipped_sheep_layouts_agree_with_the_bytecode():
    cases = [("vanilla_1_12_sheep_64.json", "bqp"), ("vanilla_1_12_sheep_wool_64.json", "bqo")]
    for name, model in cases:
        document = json.loads((ROOT / "layouts" / name).read_text(encoding="utf-8"))
        shipped = sorted((c["u"], c["v"], c["width"], c["height"], c["depth"])
                         for c in document["cubes"])
        read = sorted({(b.u, b.v, b.w, b.h, b.d) for b in boxes_for_class(_source(*SHEEP), model)})
        assert shipped == read, name


def test_shipped_cow_layout_agrees_with_the_bytecode():
    document = json.loads((ROOT / "layouts" / "vanilla_1_12_cow_64.json").read_text(encoding="utf-8"))
    # the two horns are one box spec used twice, so compare the distinct specs
    shipped = sorted({(c["u"], c["v"], c["width"], c["height"], c["depth"]) for c in document["cubes"]})
    read = sorted({(b.u, b.v, b.w, b.h, b.d) for b in boxes_for_class(_source(*COW), "bpn")})
    assert shipped == read


def test_layout_document_is_loadable():
    from mc_art.uv_layout import layout_regions

    boxes = boxes_for_class(_source(*SHEEP), "bqo")
    document = layout_document(boxes, name="sheep_fur", source="ModelSheep2",
                               texture_width=64, texture_height=32)
    regions = layout_regions(document)
    assert len(regions) == 6 * len(boxes)
    assert {r.part_id for r in regions} == {"a", "b", "c", "d", "e", "f"}
    assert all(r.preview_instances and min(r.preview_instances[0][2:]) > 0 for r in regions)


def test_transform_fields_are_derived_from_the_field_table():
    """setRotationPoint is the method that writes three floats, and the angle
    fields are the three floats declared right after them. Nothing here is a
    literal a/b/c, so it survives a different obfuscation mapping."""
    text = (FIXTURES / "javap_renderer_brs.txt").read_text(encoding="utf-8")
    point, angles = transform_fields(text)
    assert point == ["c", "d", "e"]
    assert angles == ["f", "g", "h"]


def test_the_pose_comes_back_in_degrees():
    # Math.PI / 2 is 1.5707964f in the bytecode; the renderer speaks degrees
    spec = render_spec(_source(*SHEEP), "bqp")
    body = {part["name"]: part for part in spec["parts"]}["b"]
    assert body["rot"] == {"x": 90.0}


def test_the_emitted_spec_has_every_leg():
    # the hand-typed spec that silently lost two legs is why this is generated
    spec = render_spec(_source(*SHEEP), "bqp")
    assert [part["name"] for part in spec["parts"]] == ["a", "b", "c", "d", "e", "f"]
    assert all(len(part["boxes"]) == 1 for part in spec["parts"])


def test_the_emitted_spec_agrees_with_the_reference_model():
    """The whole chain, measured in the renderer's own space.

    A radians/degrees mix-up is silent -- the torso simply stands on end and the
    picture still reads as an animal -- so compare the extent instead.
    """
    from mc_art.ingame import scene_bbox, sheep_skin

    spec = render_spec(_source(*SHEEP), "bqp")
    assert np.allclose(scene_bbox([spec])[1], scene_bbox([sheep_skin()])[1])
