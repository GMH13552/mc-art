"""Tests for multi-box block models: per-face UVs and the block-entity path.

The failure under test is "深色木桌上摊一张纸，真的是木桌吗？为什么只是稍微改改木板？":
a face with no explicit UV samples the whole texture, so a sheet of paper comes
out as stretched planks. Every test here proves the audit refuses that, and that
a correctly declared model renders as a desk with a sheet on it.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from mc_art import blockmodel


def _tile(path: Path, colour: tuple[int, int, int], grain: int = 12) -> Path:
    image = Image.new("RGBA", (16, 16), colour + (255,))
    for y in range(16):
        for x in range(16):
            if (x * 3 + y * 5) % 7 == 0:
                image.putpixel((x, y), tuple(max(0, value - grain) for value in colour) + (255,))
    image.save(path)
    return path


def _desk_spec(tmp_path: Path, *, paper_uv: list[int] | None) -> dict:
    wood = _tile(tmp_path / "example_planks.png", (120, 84, 48))
    paper = _tile(tmp_path / "example_paper.png", (228, 222, 198), grain=8)
    del wood, paper
    faces = {
        "up": {"texture": "wood", "uv": [0, 0, 16, 16]},
        "down": {"texture": "wood", "uv": [0, 0, 16, 16]},
        "north": {"texture": "wood", "uv": [0, 0, 16, 3]},
        "south": {"texture": "wood", "uv": [0, 3, 16, 6]},
        "west": {"texture": "wood", "uv": [0, 6, 16, 9]},
        "east": {"texture": "wood", "uv": [0, 9, 16, 12]},
    }
    paper_face: dict = {"texture": "paper"}
    if paper_uv is not None:
        paper_face["uv"] = paper_uv
    return {
        "name": "example_desk",
        "namespace": "examplepack",
        "textures": {
            "wood": "example_planks.png",
            "paper": "example_paper.png",
        },
        "elements": [
            {"id": "top", "from": [0, 13, 0], "to": [16, 16, 16], "faces": faces},
            {
                "id": "sheet",
                "from": [2, 16, 3],
                "to": [14, 16, 13],
                "faces": {"up": paper_face},
            },
        ],
    }


def test_a_declared_sheet_on_a_desk_passes_and_is_a_block_entity(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 12, 10])
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["passed"] is True, report["problems"]
    assert report["kind"] == "block-entity"
    assert report["elements"] == 2
    assert report["texture_use"] == {"wood": 6, "paper": 1}
    assert report["unused_textures"] == []
    sheet = next(row for row in report["faces_detail"] if row["element"] == "sheet")
    assert sheet["reason"] == "ok"
    assert sheet["uv_extent"] == [12.0, 10.0]
    assert sheet["world_extent"] == [12.0, 10.0]


def test_a_sheet_face_without_uv_is_rejected_as_a_stretched_texture(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=None)
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["passed"] is False
    message = " ".join(report["problems"])
    assert "no uv" in message
    assert "stretched planks" in message
    sheet = next(row for row in report["faces_detail"] if row["element"] == "sheet")
    assert sheet["reason"] == "uv-defaulted-on-partial-face"


def test_a_whole_tile_stretched_onto_a_small_face_is_still_rejected(tmp_path: Path) -> None:
    """The sheet is given a uv -- but the wrong one, which is the same mistake."""
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 16, 16])
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["passed"] is False
    assert any("stretched onto a 12x10 face" in problem for problem in report["problems"])
    sheet = next(row for row in report["faces_detail"] if row["element"] == "sheet")
    assert sheet["reason"] == "uv-stretched"


def test_a_uv_outside_its_texture_is_rejected(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 12, 10])
    spec["elements"][1]["faces"]["up"]["uv"] = [8, 8, 20, 18]
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["passed"] is False
    assert any("lies outside the 16x16 paper texture" in problem for problem in report["problems"])


def test_a_face_naming_an_undeclared_texture_is_rejected(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 12, 10])
    spec["elements"][1]["faces"]["up"]["texture"] = "linen"
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["passed"] is False
    assert any("is not declared in the model's texture map" in problem for problem in report["problems"])


def test_a_plain_cube_is_not_reported_as_a_block_entity(tmp_path: Path) -> None:
    _tile(tmp_path / "example_planks.png", (120, 84, 48))
    spec = {
        "name": "example_block",
        "namespace": "examplepack",
        "textures": {"all": "example_planks.png"},
        "elements": [{
            "id": "cube",
            "from": [0, 0, 0],
            "to": [16, 16, 16],
            "faces": {
                face: {"texture": "all"} for face in blockmodel.FACES
            },
        }],
    }
    report = blockmodel.audit_block_model(spec, base_dir=tmp_path)
    assert report["kind"] == "cube"
    assert report["passed"] is True, report["problems"]
    assert all(row["reason"] == "uv-default-ok-full-cube-face" for row in report["faces_detail"])


def test_the_emitted_pack_renders_a_desk_that_really_shows_wood_and_paper(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 12, 10])
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    assert written["report"]["passed"] is True
    assert Path(written["model"]).exists()
    assert Path(written["uv_map"]).exists()

    rendered = blockmodel.render_block_spec(
        spec, written["pack"], tmp_path / "desk_view.png", views=("front34", "side")
    )
    sheet = Image.open(rendered["image"]).convert("RGBA")
    assert sheet.size == (720, 320)
    counts = {"wood": 0, "paper": 0}
    for y in range(sheet.height):
        for x in range(sheet.width):
            red, green, blue, alpha = sheet.getpixel((x, y))
            if alpha < 8:
                continue
            if red > 180 and green > 170 and blue > 140 and abs(red - blue) > 18:
                counts["paper"] += 1
            elif red > 80 and red < 170 and green < red and blue < green:
                counts["wood"] += 1
    assert counts["paper"] > 200, counts
    assert counts["wood"] > 200, counts


def test_the_uv_map_artifact_explains_every_face(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 12, 10])
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    text = Path(written["uv_map"]).read_text(encoding="utf-8")
    assert "example_desk" in text
    assert "sheet" in text and "paper" in text
    assert "stretched planks" in text
    assert json.loads(Path(written["audit"]).read_text(encoding="utf-8"))["passed"] is True

# -- the artifact, not the spec ----------------------------------------------
#
# The bug: face textures were passed through as bare words. In a Minecraft model a
# bare word is a PATH, so every face resolved to `<ns>:textures/<key>.png`, showed
# nothing, and the model was unusable in game. The audit stayed green because it
# checked the SPEC's convention, and the preview stayed perfect because it reads
# the PNGs off disk. Preview right, artifact broken.

def test_the_written_model_names_its_faces_as_variables(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 10, 8])
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    document = json.loads(Path(written["model"]).read_text(encoding="utf-8"))
    textures = [
        face["texture"]
        for element in document["elements"]
        for face in (element.get("faces") or {}).values()
    ]
    assert textures, "the fixture should have faces"
    for value in textures:
        assert value.startswith("#"), (
            "a face texture must be a #variable; %r would be read as a path and the face "
            "would show nothing in game" % value
        )
        assert value[1:] in document["textures"], value


def test_a_bare_word_face_is_refused_by_reading_the_model_back(tmp_path: Path) -> None:
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 10, 8])
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    model_path = Path(written["model"])
    document = json.loads(model_path.read_text(encoding="utf-8"))
    # Exactly what the bug produced.
    document["elements"][0]["faces"]["up"]["texture"] = "wood"
    model_path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    report = blockmodel.validate_written_model(model_path)
    assert report["ok"] is False
    assert report["problems"], "a bare word must be reported"
    assert "is a PATH" in report["problems"][0]["why"]
    assert report["problems"][0]["face"] == "up"


def test_write_block_model_refuses_to_emit_an_illegal_model(tmp_path: Path) -> None:
    """The check runs on generation, so the broken file cannot reach a pack."""
    import pytest

    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 10, 8])
    # A face naming a texture the spec never declares: no #variable can be made
    # from it, so what gets written is a bare word and generation must stop.
    spec["elements"][0]["faces"]["up"]["texture"] = "not_declared"
    with pytest.raises(ValueError) as caught:
        blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    assert "not a legal Minecraft model" in str(caught.value)


def test_a_declared_resource_id_is_used_instead_of_copying(tmp_path: Path) -> None:
    """One texture, one name: an explicit id ships nothing and claims nothing new."""
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 10, 8])
    spec["textures"]["wood"] = {
        "path": spec["textures"]["wood"],
        "id": "examplepack:block/example_planks",
    }
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    document = json.loads(Path(written["model"]).read_text(encoding="utf-8"))
    assert document["textures"]["wood"] == "examplepack:block/example_planks"
    assert not (tmp_path / "pack" / "assets" / "examplepack" / "textures" / "block" / "wood.png").exists()
    # `wood` was referenced rather than copied; `paper` still copies, and that
    # difference is exactly what the report records.
    assert [item["key"] for item in written["texture_copies"]] == ["paper"]


def test_a_renaming_copy_is_reported_rather_than_silent(tmp_path: Path) -> None:
    """The key differs from the source name: allowed, but said out loud."""
    spec = _desk_spec(tmp_path, paper_uv=[0, 0, 10, 8])
    written = blockmodel.write_block_model(spec, tmp_path / "pack", base_dir=tmp_path)
    copies = {item["key"] for item in written["texture_copies"]}
    assert "wood" in copies, written["texture_copies"]
    assert "RENAMED" in written["texture_copies"][0]["note"]
