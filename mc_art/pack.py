"""Assemble a resource pack from several textures and an explicit block map.

One plan is one 16x16 texture, but a block asset is usually more than that: a
log needs an end-grain file and a side file, and they are different images. The
per-plan path can only ever emit 'cube_all', which is why a log built one
texture at a time comes out with the same face on all six sides -- and why its
isometric preview shows that, whatever the pack actually declares.

This module takes the missing step: named textures plus a declared block model,
then emits the pack, a face map, and a preview that really separates the faces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from .contracts import UvRegionSpec

# model -> (parent, required face keys, optional face keys)
MODELS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "cube_all": ("block/cube_all", ("all",), ()),
    "cube_column": ("block/cube_column", ("end", "side"), ()),
    "cube_bottom_top": ("block/cube_bottom_top", ("top", "bottom", "side"), ()),
    "cube": ("block/cube", ("top", "side"), ("bottom", "front")),
    # block/cross names its texture variable "cross"; "layer0" is the item
    # convention and would leave the model with no texture at all.
    "cross": ("block/cross", ("cross",), ()),
}


def _safe(root: Path, relative: str) -> Path:
    candidate = (root / Path(relative)).resolve()
    base = root.resolve()
    if candidate == base or base not in candidate.parents:
        raise ValueError("resource-pack path escapes the output directory: %s" % relative)
    return candidate


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _texture_paths(out: Path, namespace: str, textures: dict[str, Any]) -> dict[str, Path]:
    written: dict[str, Path] = {}
    for name, source in textures.items():
        source_path = Path(str(source))
        if not source_path.exists():
            raise ValueError("texture %r points at a missing file: %s" % (name, source_path))
        target = _safe(out, "assets/%s/textures/block/%s.png" % (namespace, name))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_path.read_bytes())
        written[name] = target
    return written


def _atlas_for_preview(faces: dict[str, Image.Image]) -> tuple[Image.Image, list[UvRegionSpec]]:
    """Lay the named faces side by side so the isometric renderer can run.

    That renderer already projects labelled top/front/side rectangles from one
    atlas onto a 2:1 cube. Composing a throwaway atlas is cheaper and less
    risky than teaching it to read several files.
    """
    order = [key for key in ("top", "front", "side") if key in faces]
    if len(order) < 3:
        raise ValueError("an isometric preview needs top, front and side")
    width = sum(faces[key].width for key in order)
    height = max(faces[key].height for key in order)
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    regions: list[UvRegionSpec] = []
    left = 0
    for key in order:
        face = faces[key]
        atlas.alpha_composite(face, (left, 0))
        regions.append(UvRegionSpec(
            id=key, part_id=key, bbox=[left, 0, left + face.width, face.height], face=key,
        ))
        left += face.width
    return atlas, regions


def _faces_for(block: dict[str, Any], textures: dict[str, Path]) -> dict[str, Path]:
    model = str(block.get("model") or "").strip()
    if model not in MODELS:
        raise ValueError("block %r names unknown model %r; known: %s" % (
            block.get("name"), model, ", ".join(sorted(MODELS))))
    _parent, required, optional = MODELS[model]
    declared = {str(k): str(v) for k, v in (block.get("faces") or {}).items()}
    missing = [key for key in required if key not in declared]
    if missing:
        raise ValueError("block %r (%s) is missing face(s): %s" % (
            block.get("name"), model, ", ".join(missing)))
    unknown = [key for key in declared if key not in required + optional]
    if unknown:
        raise ValueError("block %r (%s) declares unusable face(s): %s" % (
            block.get("name"), model, ", ".join(unknown)))
    resolved: dict[str, Path] = {}
    for key, texture_name in declared.items():
        if texture_name not in textures:
            raise ValueError("block %r face %r names undeclared texture %r" % (
                block.get("name"), key, texture_name))
        resolved[key] = textures[texture_name]
    return resolved


def build_pack(manifest: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    """Write a resource pack plus a face map and a face-correct preview."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    namespace = str(manifest.get("namespace") or "generated")
    blocks = list(manifest.get("blocks") or [])
    if not blocks:
        raise ValueError("a pack manifest needs at least one block")

    _write_json(out / "pack.mcmeta", {"pack": {
        "pack_format": int(manifest.get("pack_format") or 15),
        "description": str(manifest.get("description") or "Generated by mc-art"),
    }})
    textures = _texture_paths(out, namespace, manifest.get("textures") or {})

    face_map: list[str] = []
    warnings: list[str] = []
    previews: list[str] = []
    for block in blocks:
        name = str(block.get("name") or "").strip()
        if not name:
            raise ValueError("every block needs a name")
        model = str(block.get("model") or "").strip()
        parent, _required, _optional = MODELS[model]
        faces = _faces_for(block, textures)

        _write_json(
            _safe(out, "assets/%s/models/block/%s.json" % (namespace, name)),
            {"parent": "minecraft:" + parent,
             "textures": {key: "%s:block/%s" % (namespace, path.stem) for key, path in faces.items()}},
        )
        _write_json(
            _safe(out, "assets/%s/blockstates/%s.json" % (namespace, name)),
            {"variants": {"": {"model": "%s:block/%s" % (namespace, name)}}},
        )
        if block.get("item"):
            _write_json(
                _safe(out, "assets/%s/models/item/%s.json" % (namespace, name)),
                {"parent": "%s:block/%s" % (namespace, name)},
            )
        if model == "cube_column" and faces["end"] == faces["side"]:
            warnings.append(
                "cube_column %r uses the same texture for end and side, which is what a "
                "single-texture plan produces by accident" % name
            )

        face_map.append("%-24s -> %-22s {%s}" % (
            name, parent, ", ".join("%s=%s" % (k, p.stem) for k, p in faces.items())))

        # The preview is the point: it must show the faces the pack declares.
        if model == "cube_column":
            images = {"top": Image.open(faces["end"]).convert("RGBA"),
                      "front": Image.open(faces["side"]).convert("RGBA"),
                      "side": Image.open(faces["side"]).convert("RGBA")}
        elif model in {"cube_all", "cube_bottom_top", "cube"}:
            single = Image.open(faces.get("all") or faces.get("top")).convert("RGBA")
            images = {"top": single, "front": single, "side": single}
        else:
            images = {}
        if images:
            from .uv_layout import render_isometric_preview

            atlas, regions = _atlas_for_preview(images)
            target = _safe(out, "preview_%s.png" % name)
            render_isometric_preview(atlas, regions, scale=8).save(target, "PNG")
            previews.append(str(target))

    (out / "FACE_MAP.txt").write_text(
        "block                    model            textures\n"
        + "\n".join(face_map)
        + "\n\nA cube_column block gets its end texture on top and bottom, and its side\n"
          "texture on all four sides. A single 16x16 plan can only ever produce\n"
          "cube_all; declare the faces here instead.\n",
        encoding="utf-8",
    )
    return {
        "pack": str(out),
        "namespace": namespace,
        "blocks": [str(block.get("name")) for block in blocks],
        "textures": sorted(textures),
        "previews": previews,
        "warnings": warnings,
    }
