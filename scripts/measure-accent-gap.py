"""Measure the accent-to-base luma gap of a set of reference textures.

Why this exists. The engine's embedding band (6..24) was calibrated by taking ONE
deposit -- iron_ore's own specks -- and averaging the whole cluster. That average
is 12.24, a middling number, and using it as the standard for every asset is what
cost a real project seven rounds: an ore whose specks SHOULD jump out reads as
"too loud" against a band that was never about ores.

Measured per deposit over the eight vanilla ores, the gap runs -75.7 (redstone) to
+106.6 (gold). **None of them is inside 6..24.** The difference between the two
figures is the whole problem, so this tool reports both and says which one the band
comes from:

  * **per deposit** -- for each connected accent region, one mean accent luma
    minus one mean base luma. This is the figure to declare as
    ``accent_base_gap_min`` / ``accent_base_gap_max``.
  * **per pixel** -- every accent pixel against the base pixels touching it. This
    is the sharper figure, and it is what the boundary step gate uses. It is NOT
    the band: averaging a cluster first is exactly how a high-contrast ore became
    12.24.

    python scripts/measure-accent-gap.py <textures...>
    python scripts/measure-accent-gap.py --dir <reference root>
    python scripts/measure-accent-gap.py --dir refs --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from mc_art.style import _opaque_map, chroma, components, luma  # noqa: E402


def measure(path: Path, *, chroma_min: float = 45.0) -> dict:
    """Split a texture's pixels into accent and base, then measure both ways.

    Accent = a pixel whose chroma stands out from the local median, which is the
    same declaration-free rule the engine's `unaudited_accent` scan uses. Ore
    specks are chromatic against grey stone, so this finds them without being told
    what colour they are.
    """
    image = Image.open(path).convert("RGBA")
    pixels = _opaque_map(image)
    if not pixels:
        return {"name": path.stem, "error": "no opaque pixels"}
    width, height = image.size
    offsets = [(dx, dy) for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2) if (dx, dy) != (0, 0)]
    accent: set[tuple[int, int]] = set()
    for point, colour in pixels.items():
        x, y = point
        local = [pixels[(x + dx, y + dy)] for dx, dy in offsets if (x + dx, y + dy) in pixels]
        if len(local) < 4:
            continue
        if chroma(colour) - statistics.median([chroma(other) for other in local]) >= chroma_min:
            accent.add(point)
    base = set(pixels) - accent
    if not accent or not base:
        return {
            "name": path.stem,
            "accent_pixels": len(accent),
            "base_pixels": len(base),
            "note": "no chromatic deposit found (is this a plain material?)",
        }
    base_luma_mean = sum(luma(pixels[point]) for point in base) / len(base)

    # per deposit: one number per connected accent region
    per_deposit = []
    for group in components(accent):
        if len(group) < 2:
            continue
        group_luma = sum(luma(pixels[point]) for point in group) / len(group)
        per_deposit.append(group_luma - base_luma_mean)

    # per pixel: every accent pixel against the base pixels it touches
    per_pixel = []
    for x, y in accent:
        neighbours = [
            (x + dx, y + dy)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            if (x + dx, y + dy) in base
        ]
        if not neighbours:
            continue
        nearest = min(
            neighbours, key=lambda point: abs(luma(pixels[(x, y)]) - luma(pixels[point]))
        )
        per_pixel.append(luma(pixels[(x, y)]) - luma(pixels[nearest]))

    def summary(values: list[float]) -> dict:
        if not values:
            return {}
        return {
            "min": round(min(values), 1),
            "max": round(max(values), 1),
            "median": round(statistics.median(values), 1),
            "mean": round(sum(values) / len(values), 1),
            "count": len(values),
        }

    return {
        "name": path.stem,
        "size": "%dx%d" % (width, height),
        "accent_pixels": len(accent),
        "base_pixels": len(base),
        "base_luma_mean": round(base_luma_mean, 1),
        "per_deposit": summary(per_deposit),
        "per_pixel": summary(per_pixel),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("textures", nargs="*", help="reference PNGs")
    parser.add_argument("--dir", help="a reference root; every *.png in it")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = [Path(item) for item in args.textures]
    if args.dir:
        paths.extend(sorted(Path(args.dir).glob("*.png")))
    if not paths:
        parser.error("give textures or --dir")

    rows = []
    for path in paths:
        if not path.exists():
            print("missing: %s" % path, file=sys.stderr)
            continue
        rows.append(measure(path))

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print("%-16s %-8s %-26s %s" % ("reference", "base", "PER DEPOSIT (declare this)", "PER PIXEL (boundary)"))
    deposits: list[float] = []
    pixels: list[float] = []
    for row in rows:
        if "per_deposit" not in row:
            print("%-16s %-8s %s" % (row["name"], row.get("base_luma_mean", "-"), row.get("note", "?")))
            continue
        dep = row["per_deposit"]
        pix = row["per_pixel"]
        deposits.extend([dep["min"], dep["max"]])
        pixels.extend([pix["min"], pix["max"]])
        print("%-16s %-8s %+7.1f .. %+7.1f (med %+.1f)   %+7.1f .. %+7.1f (n=%d)"
              % (row["name"], row["base_luma_mean"], dep["min"], dep["max"], dep["median"],
                 pix["min"], pix["max"], pix["count"]))

    if deposits:
        print()
        print("DECLARE THIS -- per deposit, which is what accent_base_gap_min/max means:")
        print('  "accent_base_gap_min": %+.1f,' % min(deposits))
        print('  "accent_base_gap_max": %+.1f,' % max(deposits))
        print()
        print("For scale: the engine's default band is the ITEM band (6..24), which is")
        print("calibrated on one item's accent and does not describe a deposit. The eight")
        print("vanilla ores measure -75.7 (redstone) to +106.6 (gold) per deposit.")
        print()
        print("Per-pixel is NOT the band. Averaging a whole cluster into one number first is")
        print("exactly how iron_ore's specks became the 12.24 that mis-measured everything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
