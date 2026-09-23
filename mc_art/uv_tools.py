"""Entity UV work: find a layout, author one, and see what it produced.

The engine shipped box-decomposition support and three preview renderers, but
nothing reachable from a command line, so a live entity run (a blood slime)
bypassed all of it and hand-wrote an alpha reference instead. These three
helpers are the missing surface:

  list_layouts   which shapes already exist, and what each one covers
  layout_from_boxes   author a layout when no existing shape fits
  render_views   render the atlas so the result can actually be looked at
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .box_model import boxes_from_dict, pack_boxes
from .planfile import uv_layout_from_file
from .uv_layout import (
    layout_summary,
    render_entity_preview,
    render_front_preview,
)

# Distinguishable at 16px, and stable so two runs annotate the same way.
_BOX_COLOURS = [
    (255, 96, 96), (96, 200, 255), (150, 255, 120), (255, 200, 80),
    (220, 130, 255), (120, 255, 230), (255, 150, 60), (180, 180, 255),
]


def list_layouts(root: str | Path | None = None) -> list[dict[str, Any]]:
    """Every shipped UV layout, described enough to pick one."""
    base = Path(root).resolve() if root else Path(__file__).resolve().parents[1] / "layouts"
    found: list[dict[str, Any]] = []
    for path in sorted(base.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            regions = uv_layout_from_file(path)
        except (OSError, ValueError, TypeError):
            continue
        found.append({
            "name": path.stem,
            "path": str(path.resolve()),
            "canvas": [raw.get("texture_width"), raw.get("texture_height")],
            "source": raw.get("source"),
            "boxes": len(raw.get("cubes") or []),
            "parts": sorted({region.part_id for region in regions}),
            "faces_per_part": {
                part: sorted({region.face for region in regions if region.part_id == part})
                for part in sorted({region.part_id for region in regions})
            },
            "notes": str(raw.get("notes") or "").split(". ")[0],
        })
    return found


def layout_from_boxes(spec: Any, out_path: str | Path, canvas_width: int | None = None,
                      margin: int = 0) -> dict[str, Any]:
    """Turn a box decomposition into a UV layout and write it.

    This is the path for an object vanilla has no model for: the caller states
    its parts as axis-aligned boxes and the atlas is derived from them.
    """
    boxes = boxes_from_dict(spec)
    layout = pack_boxes(boxes, canvas_width=canvas_width, margin=margin)
    written = layout.write(out_path)
    summary = layout_summary(list(layout.regions))
    return {
        "layout": str(written),
        "canvas": list(layout.canvas),
        "boxes": [box.id for box in boxes],
        "parts": sorted({box.part_id for box in boxes}),
        "regions": len(layout.regions),
        "summary": summary,
    }


def _label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, colour: tuple[int, int, int]) -> None:
    draw.rectangle([x, y, x + 6 * len(text) + 2, y + 9], fill=(16, 16, 20))
    draw.text((x + 1, y + 1), text, fill=colour)


def annotate_layout(texture_path: str | Path, regions: list[Any], scale: int = 8) -> Image.Image:
    """Draw the atlas with every region boxed and named.

    Authoring an entity atlas by hand is guesswork without this: the atlas is
    mostly empty canvas and nothing says which cells belong to which part.
    """
    with Image.open(texture_path) as loaded:
        atlas = loaded.convert("RGBA")
    canvas = atlas.resize((atlas.width * scale, atlas.height * scale), Image.NEAREST)
    legend_height = 12 + 10 * max(1, len(regions))
    output = Image.new("RGBA", (max(canvas.width, 240), canvas.height + legend_height), (24, 24, 28, 255))
    output.alpha_composite(canvas, (0, 0))
    draw = ImageDraw.Draw(output)
    for index, region in enumerate(regions):
        colour = _BOX_COLOURS[index % len(_BOX_COLOURS)]
        left, top, right, bottom = region.bbox
        draw.rectangle(
            [left * scale, top * scale, right * scale - 1, bottom * scale - 1],
            outline=colour,
        )
        _label(draw, 4, canvas.height + 10 * index + 2,
               "%s %s [%d,%d,%d,%d]" % (region.part_id, region.face, left, top, right, bottom), colour)
    return output


def footprint_warnings(regions: list[Any]) -> list[str]:
    """Report preview boxes that state a size the renderer will ignore.

    A preview instance is a position. It used to be a size too, and two shipped
    layouts carried sizes that were simply wrong, which drew an 8x8 head at
    10x8. The renderer now ignores the size, so a wrong one is no longer
    harmful -- but it is still a lie in the file, and whoever reads it next will
    believe it. Say so rather than let it sit.
    """
    warnings: list[str] = []
    for region in regions:
        # The instance rides on the cube and is copied to all six faces, but
        # only the front face is ever placed by the previews; the other five
        # are different sizes by construction.
        if region.face != "front":
            continue
        width = region.bbox[2] - region.bbox[0]
        height = region.bbox[3] - region.bbox[1]
        for item in region.preview_instances or []:
            if (item[2], item[3]) != (width, height):
                warnings.append(
                    "%s/%s states %dx%d but the face is %dx%d; only its position is used"
                    % (region.part_id, region.face, item[2], item[3], width, height)
                )
    return warnings


def render_views(layout_path: str | Path, texture_path: str | Path, out_dir: str | Path,
                 scale: int = 8) -> dict[str, Any]:
    """Write the annotated atlas plus both entity previews."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    regions = uv_layout_from_file(layout_path)
    stem = Path(texture_path).stem
    written: dict[str, str] = {}
    annotate_layout(texture_path, regions, scale).save(out / (stem + "_uvmap.png"), "PNG")
    written["uvmap"] = str(out / (stem + "_uvmap.png"))
    with Image.open(texture_path) as loaded:
        texture = loaded.convert("RGBA")
    for name, renderer in (("front", render_front_preview), ("layers", render_entity_preview)):
        try:
            renderer(texture, regions, scale=scale).save(out / ("%s_%s.png" % (stem, name)), "PNG")
            written[name] = str(out / ("%s_%s.png" % (stem, name)))
        except (ValueError, KeyError) as exc:
            written[name + "_error"] = str(exc)
    return {
        "texture": str(texture_path),
        "layout": str(layout_path),
        "views": written,
        "warnings": footprint_warnings(regions),
    }
