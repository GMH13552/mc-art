"""Pairwise continuity metrics for several frames of one object.

Usage:
    python3 continuity.py a.png b.png c.png [...]
    python3 continuity.py --baseline src_a.png --baseline src_b.png gen_a.png gen_b.png

Reports, per pair whose silhouettes overlap enough to be the same object:
  iou              silhouette intersection over union
  exact            fraction of the union painted identically
  near             fraction within --tolerance per channel
  mean_delta       mean max-channel difference over the union

Compare a generated family against the SOURCE family it was derived from.
Vanilla's own bow frames only reach 29%-66% exact pairwise; an absolute
threshold would fail the source art.
"""
import argparse
from pathlib import Path

from PIL import Image


def pixels(path):
    with Image.open(path) as loaded:
        image = loaded.convert("RGBA")
    return {(x, y): image.getpixel((x, y)) for y in range(image.height) for x in range(image.width)}


def pairs(paths, minimum_shared, tolerance):
    loaded = [(Path(p), pixels(p)) for p in paths]
    rows = []
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            (path_a, a), (path_b, b) = loaded[i], loaded[j]
            if len(a) != len(b):
                continue
            opaque_a = {p for p, v in a.items() if v[3] >= 8}
            opaque_b = {p for p, v in b.items() if v[3] >= 8}
            union = opaque_a | opaque_b
            if not union:
                continue
            iou = len(opaque_a & opaque_b) / float(len(union))
            if iou < minimum_shared:
                continue
            exact = near = delta_total = 0
            for point in union:
                left, right = a[point], b[point]
                if left == right:
                    exact += 1
                delta = max(abs(left[k] - right[k]) for k in range(4))
                delta_total += delta
                if delta <= tolerance:
                    near += 1
            size = float(len(union))
            rows.append({
                "a": path_a.name, "b": path_b.name, "iou": round(iou, 4),
                "exact": round(exact / size, 4), "near": round(near / size, 4),
                "mean_delta": round(delta_total / size, 2),
            })
    return rows


def summarise(label, rows):
    if not rows:
        print("%-10s no overlapping pairs" % label)
        return
    print("%-10s pairs=%d  exact min=%.4f mean=%.4f | near min=%.4f mean=%.4f" % (
        label, len(rows), min(r["exact"] for r in rows), sum(r["exact"] for r in rows) / len(rows),
        min(r["near"] for r in rows), sum(r["near"] for r in rows) / len(rows)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--baseline", action="append", default=[])
    parser.add_argument("--minimum-shared", type=float, default=0.35)
    parser.add_argument("--tolerance", type=int, default=24)
    args = parser.parse_args()
    generated = pairs(args.paths, args.minimum_shared, args.tolerance)
    summarise("generated", generated)
    for row in generated:
        print("   %-22s %-22s iou=%.3f exact=%.3f near=%.3f delta=%.1f" % (
            row["a"], row["b"], row["iou"], row["exact"], row["near"], row["mean_delta"]))
    if args.baseline:
        summarise("source", pairs(args.baseline, args.minimum_shared, args.tolerance))
