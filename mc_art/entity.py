"""One model description, two outputs, and a check.

An entity asset needs the same three things however it is born:

* a **render spec** -- parts, pivots, rotations, boxes with 3D placement -- so the
  thing can be looked at in the game's own view;
* a **layout** -- the rectangles of the atlas, so the texture can be painted or
  conformed to;
* an **audit** -- proof that the paint and the rectangles agree.

Vanilla and a custom mob differ only in where the description comes from. When
the model exists, 'mc-art model' reads it out of bytecode and its texture offsets
are fixed by the game, so it arrives with u/v stated. When it does not, the author
states the boxes and the offsets become a packing decision, which this module
makes. Both end up in the same shape, and 'mc-art ingame' renders either.

The audit is the part that earns its keep. A rectangle no opaque texel lands in,
or an opaque texel that lands in no rectangle, is exactly the failure a
self-consistent-looking atlas hides -- and the one an eyeball render cannot be
trusted to catch, because a mob painted into the wrong rectangles still renders
as a mob.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "assign_offsets",
    "audit",
    "box_faces",
    "box_net",
    "format_audit",
    "layout_document",
    "load_spec",
    "plan",
    "write_plan",
]

_AXES = ("x", "y", "z")


def _number(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be a number, got %r" % (label, value)) from None


def _positive(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("%s must be an integer, got %r" % (label, value)) from None
    if result < 1:
        raise ValueError("%s must be positive, got %d" % (label, result))
    return result


def _triple(value: Any, label: str, default=(0.0, 0.0, 0.0)) -> list[float]:
    if value is None:
        return list(default)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("%s must be three numbers" % label)
    return [_number(item, label) for item in value]


def load_spec(payload: Any) -> dict[str, Any]:
    """Validate a model description into the canonical shape.

    {"name", "tex": [w, h], "parts": [{"name", "pivot", "rot", "boxes":
    [{"at", "w", "h", "d", "inflate", "u"?, "v"?}]}]}

    Angles are degrees, model space is y down and z backward, one unit is 1/16
    block, and "at" is the offset that would be handed to addBox.
    """
    if not isinstance(payload, dict):
        raise ValueError("a model spec must be an object")
    tex = payload.get("tex") or payload.get("texture_size") or [64, 32]
    if not isinstance(tex, (list, tuple)) or len(tex) != 2:
        raise ValueError("tex must be [width, height]")
    raw_parts = payload.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValueError("a model spec needs a non-empty parts list")

    parts: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_parts):
        if not isinstance(raw, dict):
            raise ValueError("each part must be an object")
        name = str(raw.get("name") or "part_%d" % (index + 1)).strip()
        raw_boxes = raw.get("boxes")
        if not isinstance(raw_boxes, list) or not raw_boxes:
            raise ValueError("part %s needs a non-empty boxes list" % name)
        rotation = {}
        for axis, value in (raw.get("rot") or {}).items():
            if axis not in _AXES:
                raise ValueError("part %s has an unknown rotation axis %r" % (name, axis))
            rotation[axis] = _number(value, "%s.rot.%s" % (name, axis))
        boxes = []
        for raw_box in raw_boxes:
            if not isinstance(raw_box, dict):
                raise ValueError("each box must be an object")
            box = {
                "at": _triple(raw_box.get("at", raw_box.get("origin")),
                              "%s box at" % name),
                "w": _positive(raw_box.get("w", raw_box.get("width")), "%s box w" % name),
                "h": _positive(raw_box.get("h", raw_box.get("height")), "%s box h" % name),
                "d": _positive(raw_box.get("d", raw_box.get("depth")), "%s box d" % name),
                "inflate": _number(raw_box.get("inflate", 0.0), "%s box inflate" % name),
            }
            if raw_box.get("u") is not None and raw_box.get("v") is not None:
                box["u"] = int(raw_box["u"])
                box["v"] = int(raw_box["v"])
            boxes.append(box)
        parts.append({"name": name,
                      "pivot": _triple(raw.get("pivot"), "%s pivot" % name),
                      "rot": rotation, "boxes": boxes})
    spec = {"name": str(payload.get("name") or "entity").strip(),
            "tex": [_positive(tex[0], "tex width"), _positive(tex[1], "tex height")],
            "parts": parts}
    # pass the provenance through: a spec that came out of the game keeps the
    # texture it was read from and the units it was written in, so replanning it
    # does not quietly strand the next command
    for key in ("texture", "units", "source_class", "renderer_class"):
        if key in payload:
            spec[key] = payload[key]
    return spec


def box_net(box: dict[str, Any]) -> tuple[int, int]:
    """The atlas rectangle a box's six faces occupy: 2d + 2w by d + h."""
    return (2 * box["d"] + 2 * box["w"], box["d"] + box["h"])


def box_faces(box: dict[str, Any]) -> dict[str, list[int]]:
    """The six half-open [left, top, right, bottom] rectangles, laid out the way
    ModelBox does it: top and bottom above the side strip, and the strip as
    left, front, right, back."""
    u, v, w, h, d = box["u"], box["v"], box["w"], box["h"], box["d"]
    return {
        "top": [u + d, v, u + d + w, v + d],
        "bottom": [u + d + w, v, u + d + 2 * w, v + d],
        "left": [u, v + d, u + d, v + d + h],
        "front": [u + d, v + d, u + d + w, v + d + h],
        "right": [u + d + w, v + d, u + 2 * d + w, v + d + h],
        "back": [u + 2 * d + w, v + d, u + 2 * d + 2 * w, v + d + h],
    }


def _next_power_of_two(value: int) -> int:
    size = 16
    while size < value:
        size *= 2
    return size


def assign_offsets(spec: dict[str, Any], canvas_width: int | None = None):
    """Give every box that does not state one a texture offset.

    A box carrying u/v keeps them: when the model came out of the game those are
    not a packing choice, they are what the renderer reads. The rest are shelf
    packed into the space that is left, in declaration order, and the canvas
    grows to fit.
    """
    claimed: list[tuple[int, int, int, int]] = []
    pending: list[dict[str, Any]] = []
    for part in spec["parts"]:
        for box in part["boxes"]:
            if "u" in box and "v" in box:
                width, height = box_net(box)
                claimed.append((box["u"], box["v"], width, height))
            else:
                pending.append(box)

    if pending:
        if canvas_width is None:
            canvas_width = max(64, max(box_net(box)[0] for box in pending))

        def collides(u: int, v: int, width: int, height: int) -> bool:
            for left, top, span, tall in claimed:
                if u < left + span and left < u + width and v < top + tall and top < v + height:
                    return True
            return False

        x = y = shelf = 0
        for box in pending:
            width, height = box_net(box)
            while True:
                if x + width > canvas_width:
                    x, y, shelf = 0, y + shelf, 0
                if not collides(x, y, width, height):
                    box["u"], box["v"] = x, y
                    claimed.append((x, y, width, height))
                    x += width
                    shelf = max(shelf, height)
                    break
                x += 1

    if not claimed:
        raise ValueError("the model has no boxes")
    width = _next_power_of_two(max(left + span for left, _, span, _ in claimed))
    height = _next_power_of_two(max(top + tall for _, top, _, tall in claimed))
    # a declared atlas size is a floor, never a ceiling: when the offsets came
    # from the game it is the atlas the game uses, and when they were packed the
    # caller may still want room for the parts they have not described yet
    width = max(width, spec["tex"][0])
    height = max(height, spec["tex"][1])
    spec["tex"] = [width, height]
    return spec, (width, height)


def _trim(value: Any) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else ("%g" % number)


def layout_document(spec: dict[str, Any]) -> dict[str, Any]:
    """The painted rectangles, as a layout a painter or 'mc-art uv' can read."""
    cubes = []
    for part in spec["parts"]:
        for index, box in enumerate(part["boxes"]):
            note = "addBox(%s) pivot=%s" % (
                ",".join(_trim(value) for value in box["at"]),
                ",".join(_trim(value) for value in part["pivot"]))
            if part["rot"]:
                note += " rot=" + ",".join("%s%s" % (axis, _trim(part["rot"][axis]))
                                           for axis in _AXES if axis in part["rot"])
            cubes.append({
                "id": "%s_%d" % (part["name"], index + 1),
                "part_id": part["name"],
                "u": box["u"], "v": box["v"],
                "width": box["w"], "height": box["h"], "depth": box["d"],
                "notes": note,
            })
    return {"format": "minecraft_1_12_modelrenderer_cube_uv",
            "source": "mc_art.entity (%s)" % spec["name"],
            "texture_width": spec["tex"][0], "texture_height": spec["tex"][1],
            "notes": "Rectangles derived from the model's own boxes. Every opaque "
                     "texel has to land inside one of them; check with "
                     "'mc-art entity --atlas' or 'mc-art ingame'.",
            "cubes": cubes}


def audit(spec: dict[str, Any], texture) -> dict[str, Any]:
    """Check a painted atlas against the model's rectangles.

    'stray' is the number that matters: opaque texels no box samples, meaning the
    paint sits where the game never looks. 'empty' is a rectangle with nothing in
    it, which is often a face the player can never see and sometimes a part
    nobody painted, so it is reported rather than failed on.
    """
    array = _alpha_grid(texture)
    height = len(array)
    width = len(array[0]) if height else 0
    painted = {(x, y) for y in range(height) for x in range(width) if array[y][x]}
    claimed: set[tuple[int, int]] = set()
    per_box = []
    for part in spec["parts"]:
        for index, box in enumerate(part["boxes"]):
            here: set[tuple[int, int]] = set()
            for left, top, right, bottom in box_faces(box).values():
                for y in range(top, bottom):
                    for x in range(left, right):
                        if 0 <= x < width and 0 <= y < height:
                            here.add((x, y))
            claimed |= here
            per_box.append({"id": "%s_%d" % (part["name"], index + 1),
                            "cells": len(here), "empty": len(here - painted)})
    stray = painted - claimed
    return {
        "painted": len(painted),
        "claimed": len(claimed),
        "stray": len(stray),
        "empty": len(claimed - painted),
        "covered": (len(painted & claimed) / len(painted)) if painted else 0.0,
        "stray_pixels": sorted(stray)[:64],
        "boxes": per_box,
    }


def _alpha_grid(texture) -> list[list[bool]]:
    """Accept a PIL image or an (h, w, 4) array in 0..1 -- the renderer hands
    over the latter, a painter the former."""
    if hasattr(texture, "convert"):
        pixels = texture.convert("RGBA").load()
        width, height = texture.size
        return [[pixels[x, y][3] > 0 for x in range(width)] for y in range(height)]
    return [[bool(pixel[3] > 0) for pixel in row] for row in texture]


def format_audit(report: dict[str, Any]) -> str:
    return ("%d painted, %d claimed, %.1f%% covered, %d stray, %d empty"
            % (report["painted"], report["claimed"], 100.0 * report["covered"],
               report["stray"], report["empty"]))


def plan(spec_payload: Any, canvas_width: int | None = None) -> dict[str, Any]:
    """The whole authoring step: validate, place, and emit both documents."""
    spec = load_spec(spec_payload)
    spec, canvas = assign_offsets(spec, canvas_width=canvas_width)
    return {"spec": spec, "canvas": list(canvas), "layout": layout_document(spec)}


def write_plan(result: dict[str, Any], out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    layout_path = out / "layout.json"
    spec_path = out / "model.json"
    layout_path.write_text(json.dumps(result["layout"], indent=1) + chr(10), encoding="utf-8")
    spec_path.write_text(json.dumps(result["spec"], indent=1) + chr(10), encoding="utf-8")
    return {"layout": str(layout_path), "model": str(spec_path)}
