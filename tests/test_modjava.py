"""The mod side: making the model is ours, so the Java is generated from the
same spec the atlas is planned against. Editing the Java and reading it back is
the loop that keeps the two from drifting.
"""
import pytest

from mc_art.entity import plan
from mc_art.modjava import class_name_from, emit, parse

MOB = {
    "name": "test_mob",
    "tex": [64, 32],
    "parts": [
        {"name": "head", "pivot": [0, 6, -8], "rot": {},
         "boxes": [{"at": [-3, -4, -6], "w": 6, "h": 6, "d": 8}]},
        {"name": "body", "pivot": [0, 5, 2], "rot": {"x": 90},
         "boxes": [{"at": [-4, -10, -7], "w": 8, "h": 16, "d": 6, "inflate": 1.75}]},
    ],
}

HAND_WRITTEN = """
public class ModelThing extends ModelBase {
    public ModelRenderer head;
    public ModelRenderer body;
    public ModelThing() {
        this.head = new ModelRenderer(this, 0, 0);
        this.head.addBox(-4.0F, -4.0F, -6.0F, 8, 8, 6);
        this.head.setRotationPoint(0.0F, 4.0F, -8.0F);
        this.body = new ModelRenderer(this, 28, 8);
        this.body.addBox(-5.0F, -10.0F, -7.0F, 10, 16, 8);
        this.body.setRotationPoint(0.0F, 5.0F, 2.0F);
        this.body.rotateAngleX = (float)Math.PI / 2F;
    }
}
"""


def _shapes(spec):
    return (sorted((part["name"], box["u"], box["v"], box["w"], box["h"], box["d"],
                    tuple(box["at"]), box["inflate"])
                   for part in spec["parts"] for box in part["boxes"]),
            sorted((part["name"], tuple(part["pivot"]), tuple(sorted(part["rot"].items())))
                   for part in spec["parts"]))


def test_spec_to_java_to_spec_is_the_identity():
    spec = plan(MOB)["spec"]
    source = emit(spec, class_name="ModelTest", package="com.example.client")
    back = parse(source, name="ModelTest")
    assert _shapes(back) == _shapes(spec)


def test_the_java_speaks_radians_and_the_spec_speaks_degrees():
    spec = plan(MOB)["spec"]
    source = emit(spec, class_name="ModelTest")
    assert "rotateAngleX = 1.570796F;" in source
    back = parse(source)
    body = {part["name"]: part for part in back["parts"]}["body"]
    assert body["rot"] == {"x": 90.0}


def test_a_hand_written_model_reads_the_same_way():
    spec = parse(HAND_WRITTEN, name="ModelThing")
    head, body = spec["parts"]
    assert (head["boxes"][0]["w"], head["boxes"][0]["h"], head["boxes"][0]["d"]) == (8, 8, 6)
    assert head["pivot"] == [0.0, 4.0, -8.0]
    assert body["rot"]["x"] == 90.0


def test_the_generated_class_names_its_texture():
    spec = plan(MOB)["spec"]
    spec["texture"] = "textures/entity/test_mob.png"
    source = emit(spec, class_name="ModelTest", texture=spec["texture"])
    assert "textures/entity/test_mob.png (64x32)" in source


def test_the_generated_class_renders_every_part():
    spec = plan(MOB)["spec"]
    source = emit(spec, class_name="ModelTest")
    assert "this.head.render(scale);" in source
    assert "this.body.render(scale);" in source


def test_computed_geometry_is_refused_rather_than_guessed():
    """A model that computes its offsets at runtime cannot be read from source;
    the compiled class can, which is what mc-art model --jar is for."""
    with pytest.raises(ValueError):
        parse("public class X { void f() { this.a.addBox(x, y, z, 1, 1, 1); } }")


def test_class_name_from_a_path():
    assert class_name_from("/tmp/src/ModelBloodSlime.java") == "ModelBloodSlime"
    assert class_name_from("ModelCow") == "ModelCow"
