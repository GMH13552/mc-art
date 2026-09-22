"""Magnify pixel-art sprites into a labelled contact sheet.

Usage:
    python3 magnify.py '{"out":"/tmp/x.png","cols":4,"entries":[["label","path"]]}'

Scale defaults to 12. Reads a JSON spec so a shell quoting mistake cannot
silently drop a tile.
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

DEFAULT_SCALE = 12


def tile(path, scale):
    with Image.open(path) as loaded:
        image = loaded.convert("RGBA")
    return image.resize((image.width * scale, image.height * scale), Image.NEAREST)


def sheet(entries, out, cols=0, scale=DEFAULT_SCALE):
    tiles = [(label, tile(path, scale)) for label, path in entries]
    if not tiles:
        raise SystemExit("no entries")
    cols = cols or len(tiles)
    pad, label_h = 10, 22
    cell_w = max(image.width for _label, image in tiles) + pad * 2
    cell_h = max(image.height for _label, image in tiles) + pad * 2 + label_h
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new("RGBA", (cell_w * cols, cell_h * rows), (26, 26, 32, 255))
    draw = ImageDraw.Draw(canvas)
    for index, (label, image) in enumerate(tiles):
        cx = (index % cols) * cell_w
        cy = (index // cols) * cell_h
        draw.text((cx + pad, cy + 5), label, fill=(235, 235, 240, 255))
        canvas.alpha_composite(image, (cx + pad, cy + label_h + pad))
    canvas.save(out)
    print("%s %s" % (out, canvas.size))


if __name__ == "__main__":
    spec = json.loads(sys.argv[1])
    sheet(
        [(str(a), str(b)) for a, b in spec["entries"]],
        spec["out"],
        cols=int(spec.get("cols") or 0),
        scale=int(spec.get("scale") or DEFAULT_SCALE),
    )
