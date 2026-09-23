"""The pack manifest: several textures, one face-correct block.

A live eyeball-tree run built a log from separate end-grain and side plans and
got a pack whose isometric preview showed the side texture on all six faces.
The pack was right; the single-texture path simply cannot express "end differs
from side", so this is the test that it now can.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from mc_art.pack import build_pack


def _solid(path: Path, colour: tuple[int, int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (16, 16), colour)
    image.save(path, "PNG")
    return path


def _manifest(tmp_path: Path) -> dict:
    end = _solid(tmp_path / "src" / "log_top.png", (30, 200, 60, 255))
    side = _solid(tmp_path / "src" / "log_side.png", (200, 30, 30, 255))
    plant = _solid(tmp_path / "src" / "sapling.png", (240, 240, 120, 255))
    return {
        "namespace": "eyeballtree",
        "pack_format": 15,
        "textures": {
            "log_top": str(end),
            "log_side": str(side),
            "sapling": str(plant),
        },
        "blocks": [
            {"name": "eyeball_log", "model": "cube_column",
             "faces": {"end": "log_top", "side": "log_side"}, "item": True},
            {"name": "eyeball_sapling", "model": "cross",
             "faces": {"cross": "sapling"}, "item": True},
        ],
    }


def test_a_column_block_keeps_its_end_and_side_distinct(tmp_path: Path) -> None:
    result = build_pack(_manifest(tmp_path), tmp_path / "pack")

    model = json.loads(
        (tmp_path / "pack" / "assets" / "eyeballtree" / "models" / "block" / "eyeball_log.json")
        .read_text(encoding="utf-8")
    )
    assert model["parent"] == "minecraft:block/cube_column"
    assert model["textures"]["end"] == "eyeballtree:block/log_top"
    assert model["textures"]["side"] == "eyeballtree:block/log_side"
    assert result["warnings"] == [], "distinct end and side must not warn"


def test_a_column_preview_shows_both_faces(tmp_path: Path) -> None:
    """This is the bug: the preview used to show the side texture six times."""
    result = build_pack(_manifest(tmp_path), tmp_path / "pack")
    preview = Image.open(result["previews"][0]).convert("RGBA")
    colours = {pixel[:3] for pixel in preview.get_flattened_data() if pixel[3] >= 8}
    assert any(g > 150 and r < 100 for r, g, _b in colours), "the end grain must be visible on top"
    assert any(r > 150 and g < 100 for r, g, _b in colours), "the side must be visible on the sides"


def test_a_cross_block_is_a_block_not_an_item(tmp_path: Path) -> None:
    """A live sapling shipped into textures/item, where the game never looks."""
    build_pack(_manifest(tmp_path), tmp_path / "pack")
    root = tmp_path / "pack" / "assets" / "eyeballtree"
    assert (root / "textures" / "block" / "sapling.png").exists()
    assert not (root / "textures" / "item" / "sapling.png").exists()
    assert (root / "blockstates" / "eyeball_sapling.json").exists()
    model = json.loads((root / "models" / "block" / "eyeball_sapling.json").read_text(encoding="utf-8"))
    assert model["parent"] == "minecraft:block/cross"
    assert model["textures"]["cross"] == "eyeballtree:block/sapling"


def test_the_face_map_names_every_texture(tmp_path: Path) -> None:
    result = build_pack(_manifest(tmp_path), tmp_path / "pack")
    face_map = (Path(result["pack"]) / "FACE_MAP.txt").read_text(encoding="utf-8")
    assert "eyeball_log" in face_map and "end=log_top" in face_map and "side=log_side" in face_map
    assert "cube_column" in face_map


def test_a_single_texture_column_is_reported_not_silently_shipped(tmp_path: Path) -> None:
    """What one plan per texture produces by accident."""
    same = _solid(tmp_path / "src" / "only.png", (120, 90, 60, 255))
    manifest = {
        "namespace": "demo",
        "textures": {"only": str(same)},
        "blocks": [{"name": "log", "model": "cube_column",
                    "faces": {"end": "only", "side": "only"}}],
    }
    result = build_pack(manifest, tmp_path / "pack")
    assert result["warnings"], "an end that equals its side is the bug, say so"


def test_a_declared_face_must_name_a_declared_texture(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["blocks"][0]["faces"]["end"] = "missing"
    try:
        build_pack(manifest, tmp_path / "pack")
    except ValueError as exc:
        assert "undeclared texture" in str(exc)
    else:  # pragma: no cover - the manifest is wrong on purpose
        raise AssertionError("an undeclared face texture must be rejected")


def test_a_missing_required_face_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    manifest["blocks"][0]["faces"] = {"end": "log_top"}
    try:
        build_pack(manifest, tmp_path / "pack")
    except ValueError as exc:
        assert "missing face" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("cube_column without a side must be rejected")
