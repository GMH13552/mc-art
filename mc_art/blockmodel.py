"""Block models with real geometry: several boxes, several textures, real UVs.

A 16x16 plan is one texture on one cube. That is the right answer for stone and
planks and the wrong answer for anything a person would call a *block entity* --
a desk with a sheet of paper lying on it, a lectern, an altar, a jar. Those are
not a recoloured plank: they are several boxes, each face sampling its own
rectangle of a texture that was painted for that face.

The failure this module exists to prevent is the one where the engine "just
changes the planks a bit": a paper face left without an explicit ``uv`` inherits
the whole texture, so a plank tile gets stretched across the sheet and the
result reads as wood. The audit below refuses that in three separate ways --
missing UV, UV outside its texture, and UV whose aspect does not match the face
it is mapped onto -- and then renders the model through the game's own camera so
the verdict can be looked at rather than trusted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw

# One block unit is one texel at the vanilla 16x16 resolution. That identity is
# what makes "is this UV stretched?" answerable without naming any object.
DEFAULT_TEXELS_PER_BLOCK = 16
FACES = ("down", "up", "north", "south", "west", "east")


def _texture_size(path: Path) -> tuple[int, int] | None:
    try:
        with Image.open(path) as loaded:
            return loaded.size
    except (OSError, ValueError):
        return None


def _face_extent(element: dict[str, Any], face: str) -> tuple[float, float]:
    """The two world-space side lengths of one face of one box, in block units."""
    start = [float(value) for value in element["from"]]
    end = [float(value) for value in element["to"]]
    sizes = [abs(end[index] - start[index]) for index in range(3)]
    if face in {"down", "up"}:
        return sizes[0], sizes[2]
    if face in {"north", "south"}:
        return sizes[0], sizes[1]
    return sizes[2], sizes[1]


def audit_block_model(
    spec: dict[str, Any],
    *,
    base_dir: str | Path | None = None,
    tolerance: float = 1.0,
    require_uv: bool = True,
) -> dict[str, Any]:
    """Check a multi-box block model's UV contract face by face.

    ``tolerance`` is in texels: a 12-texel-wide face may sample a 12-texel-wide
    UV rectangle plus or minus this. Nothing here knows what the block *is*;
    it only checks that every face samples a rectangle of the right shape out
    of the texture the author named for it.
    """
    root = Path(base_dir) if base_dir is not None else Path(".")
    textures = {
        str(name): str(value) for name, value in (spec.get("textures") or {}).items()
    }
    resolved_sizes: dict[str, tuple[int, int] | None] = {}
    for name, relative in textures.items():
        candidate = Path(relative)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved_sizes[name] = _texture_size(candidate) if candidate.exists() else None

    elements = list(spec.get("elements") or [])
    problems: list[str] = []
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    if not elements:
        problems.append("the model has no elements: a block-entity model is a list of boxes, not a texture")

    texture_use: dict[str, int] = {}
    for index, element in enumerate(elements):
        element_id = str(element.get("id") or "element_%d" % index)
        if "from" not in element or "to" not in element:
            problems.append("%s: every element needs from/to" % element_id)
            continue
        start = [float(value) for value in element["from"]]
        end = [float(value) for value in element["to"]]
        sizes = [abs(end[i] - start[i]) for i in range(3)]
        flat_axes = [axis for axis, size in zip("xyz", sizes) if size <= 0.0]
        faces = element.get("faces")
        if not isinstance(faces, dict) or not faces:
            problems.append("%s: the element declares no faces" % element_id)
            continue
        if flat_axes and len(flat_axes) > 1:
            problems.append(
                "%s: the box collapses on %s, so it has no drawable face"
                % (element_id, ", ".join(flat_axes))
            )
        for face, data in faces.items():
            if face not in FACES:
                problems.append("%s: %r is not a cube face" % (element_id, face))
                continue
            if not isinstance(data, dict):
                problems.append("%s/%s: a face must be an object" % (element_id, face))
                continue
            texture_name = data.get("texture")
            row: dict[str, Any] = {
                "element": element_id,
                "face": face,
                "texture": texture_name,
                "world_extent": [round(value, 3) for value in _face_extent(element, face)],
            }
            if texture_name is None:
                problems.append("%s/%s: no texture named for this face" % (element_id, face))
                row["reason"] = "texture-missing"
                rows.append(row)
                continue
            if str(texture_name) not in textures:
                problems.append(
                    "%s/%s: texture %r is not declared in the model's texture map"
                    % (element_id, face, texture_name)
                )
                row["reason"] = "texture-undeclared"
                rows.append(row)
                continue
            texture_use[str(texture_name)] = texture_use.get(str(texture_name), 0) + 1
            size = resolved_sizes.get(str(texture_name))
            world_width, world_height = _face_extent(element, face)
            full_cube_face = abs(world_width - 16.0) <= 1e-6 and abs(world_height - 16.0) <= 1e-6
            uv = data.get("uv")
            if uv is None:
                if require_uv and not full_cube_face:
                    problems.append(
                        "%s/%s: no uv. A %.0fx%.0f face would inherit the whole %s texture, "
                        "which is how a sheet of paper comes out as stretched planks; "
                        "declare uv [u1,v1,u2,v2] for the rectangle this face really samples"
                        % (element_id, face, world_width, world_height, texture_name)
                    )
                    row["reason"] = "uv-defaulted-on-partial-face"
                elif full_cube_face:
                    row["reason"] = "uv-default-ok-full-cube-face"
                else:
                    row["reason"] = "uv-omitted"
                rows.append(row)
                continue
            try:
                u1, v1, u2, v2 = (float(value) for value in uv)
            except (TypeError, ValueError):
                problems.append("%s/%s: uv must be [u1,v1,u2,v2]" % (element_id, face))
                row["reason"] = "uv-malformed"
                rows.append(row)
                continue
            uv_width, uv_height = abs(u2 - u1), abs(v2 - v1)
            row.update({"uv": [u1, v1, u2, v2], "uv_extent": [uv_width, uv_height]})
            if uv_width <= 0 or uv_height <= 0:
                problems.append("%s/%s: uv rectangle has no area" % (element_id, face))
                row["reason"] = "uv-empty"
                rows.append(row)
                continue
            if size is not None:
                if max(u1, u2) > size[0] or max(v1, v2) > size[1] or min(u1, v1, u2, v2) < 0:
                    problems.append(
                        "%s/%s: uv %s lies outside the %dx%d %s texture"
                        % (element_id, face, [u1, v1, u2, v2], size[0], size[1], texture_name)
                    )
                    row["reason"] = "uv-outside-texture"
                    rows.append(row)
                    continue
            else:
                notes.append(
                    "%s: texture %r was not readable, so its uv bounds could not be checked"
                    % (element_id, texture_name)
                )
            # A face samples a texture rectangle of its own shape. A 12x2 desk
            # top band is a 12x2 UV rectangle, not the whole 16x16 tile.
            if (
                abs(uv_width - world_width) > tolerance
                or abs(uv_height - world_height) > tolerance
            ):
                problems.append(
                    "%s/%s: uv %.0fx%.0f is stretched onto a %.0fx%.0f face "
                    "(ratio %.2f vs %.2f)"
                    % (
                        element_id, face, uv_width, uv_height, world_width, world_height,
                        uv_width / max(uv_height, 1e-6),
                        world_width / max(world_height, 1e-6),
                    )
                )
                row["reason"] = "uv-stretched"
            else:
                row["reason"] = "ok"
            rows.append(row)
        if flat_axes:
            notes.append(
                "%s: flat on %s (a zero-thickness plane; correct for a sheet, wrong for a solid part)"
                % (element_id, ", ".join(flat_axes))
            )
        if sizes and max(sizes) < 6.0:
            notes.append(
                "%s: %.0fx%.0fx%.0f block units, too thin to read as a solid part"
                % (element_id, sizes[0], sizes[1], sizes[2])
            )

    multi = len(elements) > 1
    block_entity = multi or any(
        any(
            abs(float(element["from"][axis])) > 0.01 or abs(float(element["to"][axis]) - 16.0) > 0.01
            for axis in range(3)
        )
        for element in elements
        if "from" in element and "to" in element
    )
    return {
        "model": str(spec.get("name") or ""),
        "kind": "block-entity" if block_entity else "cube",
        "elements": len(elements),
        "faces": sum(len(element.get("faces") or {}) for element in elements),
        "textures": sorted(textures),
        "texture_use": texture_use,
        "unused_textures": sorted(set(textures) - set(texture_use)),
        "faces_detail": rows,
        "problem_count": len(problems),
        "problems": problems,
        "notes": notes,
        "passed": not problems,
        "meaning": (
            "every face must sample a same-shaped rectangle of a texture declared for it; "
            "a face with no uv on a partial box is a stretched texture, not a default"
        ),
    }


def write_block_model(
    spec: dict[str, Any],
    out_dir: str | Path,
    *,
    base_dir: str | Path | None = None,
    pack_format: int = 15,
    description: str = "Generated by mc-art",
) -> dict[str, Any]:
    """Write a resource pack holding the model, its textures and its audit.

    The emitted model is plain Minecraft JSON (``elements`` with per-face
    ``uv``), so it loads in the game and in this skill's own game-view renderer
    without a translation step.
    """
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    namespace = str(spec.get("namespace") or "examplepack")
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError("a block model needs a name")
    source_dir = Path(base_dir) if base_dir is not None else root

    audit = audit_block_model(spec, base_dir=source_dir)

    assets = root / "assets" / namespace
    textures_dir = assets / "textures" / "block"
    models_dir = assets / "models" / "block"
    items_dir = assets / "models" / "item"
    for directory in (textures_dir, models_dir, items_dir, assets / "blockstates"):
        directory.mkdir(parents=True, exist_ok=True)

    written_textures: dict[str, str] = {}
    for texture_name, relative in (spec.get("textures") or {}).items():
        candidate = Path(str(relative))
        if not candidate.is_absolute():
            candidate = source_dir / candidate
        if not candidate.exists():
            raise ValueError("texture %r points at a missing file: %s" % (texture_name, candidate))
        target = textures_dir / (str(texture_name) + ".png")
        target.write_bytes(candidate.read_bytes())
        written_textures[str(texture_name)] = str(target)

    model_document: dict[str, Any] = {
        "textures": {
            str(key): "%s:block/%s" % (namespace, key) for key in written_textures
        },
        "elements": spec.get("elements") or [],
    }
    if spec.get("parent"):
        model_document["parent"] = str(spec["parent"])
    if spec.get("display"):
        model_document["display"] = spec["display"]
    model_path = models_dir / (name + ".json")
    model_path.write_text(
        json.dumps(model_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    blockstate_path = assets / "blockstates" / (name + ".json")
    blockstate_path.write_text(
        json.dumps(
            {"variants": {"": {"model": "%s:block/%s" % (namespace, name)}}},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    item_path = items_dir / (name + ".json")
    item_path.write_text(
        json.dumps({"parent": "%s:block/%s" % (namespace, name)}, indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "pack.mcmeta").write_text(
        json.dumps(
            {"pack": {"pack_format": int(pack_format), "description": description}},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    audit_path = root / "block_audit.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    face_lines = [
        "%-14s %-6s %-10s %-12s %-8s %s" % (
            row["element"], row["face"], row.get("texture"), row.get("uv") or "-",
            "%gx%g" % tuple(row["world_extent"]), row.get("reason", ""),
        )
        for row in audit["faces_detail"]
    ]
    (root / "UV_MAP.txt").write_text(
        "model %s (%s): %d element(s), %d face(s)\n\n"
        "element        face   texture    uv           world    verdict\n"
        % (name, audit["kind"], audit["elements"], audit["faces"])
        + "\n".join(face_lines)
        + "\n\nA face with no uv on a partial box samples the whole texture. That is\n"
          "how a sheet of paper on a desk comes out as stretched planks.\n",
        encoding="utf-8",
    )
    return {
        "pack": str(root),
        "model": str(model_path),
        "blockstate": str(blockstate_path),
        "item_model": str(item_path),
        "audit": str(audit_path),
        "uv_map": str(root / "UV_MAP.txt"),
        "textures": written_textures,
        "report": audit,
    }


def render_block_spec(
    spec: dict[str, Any],
    pack_dir: str | Path,
    out_path: str | Path,
    *,
    views: Sequence[str] = ("front34", "side"),
    viewport: tuple[int, int] = (360, 320),
    radius: float = 2.6,
    target: tuple[float, float, float] = (0.5, 0.5, 0.4),
) -> dict[str, Any]:
    """Render the emitted model through the game's own camera, one panel per view.

    This is the step that makes "did the desk actually come out as a desk?"
    answerable: it rasterises the declared elements and their declared UVs with
    the same face corner order and face shading the game uses, so a stretched
    sheet is visible as a stretched sheet.
    """
    from .ingame import VIEWS, Camera, TextureSource, load_block_model, render_block_model

    name = str(spec.get("name") or "")
    pack = Path(pack_dir)
    namespace = str(spec.get("namespace") or "examplepack")
    panels: list[Path] = []
    with TextureSource(pack, namespace=namespace) as source:
        model = load_block_model(source, name)
        for view in views:
            direction = VIEWS.get(view, VIEWS["front34"])
            norm = max(sum(value * value for value in direction) ** 0.5, 1e-6)
            position = tuple(
                target[index] + direction[index] / norm * radius for index in range(3)
            )
            camera = Camera(position, target, viewport=viewport)
            panel = pack / ("view_%s.png" % view)
            render_block_model(model, source, panel, camera)
            panels.append(panel)
    sheet = Image.new("RGBA", (viewport[0] * len(panels), viewport[1]), (18, 18, 18, 255))
    for index, panel in enumerate(panels):
        with Image.open(panel) as loaded:
            sheet.alpha_composite(loaded.convert("RGBA"), (index * viewport[0], 0))
    draw = ImageDraw.Draw(sheet)
    for index, view in enumerate(views):
        draw.text((index * viewport[0] + 4, 4), view.upper(), fill=(240, 240, 240, 255))
    target_path = Path(out_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target_path, "PNG")
    return {"image": str(target_path), "panels": [str(panel) for panel in panels]}
