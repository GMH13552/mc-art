"""Deterministic frame and palette metrics. No model, no judgement."""

from __future__ import annotations

import colorsys
import re
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image


_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")

def sprite_palette_profile(sprite_path: str | Path, limit: int = 12) -> dict[str, Any]:
    """A comparison-stable description of a rendered sprite's colours.

    Exact RGB counting is deliberately avoided. Pixel art carries many
    near-identical shades, so two textures that plainly belong to one family
    can share almost no exact colour: a live run scored 0.09 exact-RGB overlap
    on a pair whose mean nearest-neighbour distance was 7 out of 441. Colours
    are therefore quantised to 16 levels per channel, and the saturation and
    brightness split is measured separately, because "the sibling drifted to
    grey" is a real failure that a palette intersection alone can miss.
    """
    with Image.open(sprite_path) as loaded:
        image = loaded.convert("RGBA")
    pixels = [
        (red, green, blue)
        for red, green, blue, alpha in image.get_flattened_data()
        if alpha >= 8
    ]
    if not pixels:
        return {"opaque": 0, "quantised": [], "grey_ratio": 0.0, "dark_ratio": 0.0, "mean_saturation": 0.0}
    quantised = Counter((red >> 4, green >> 4, blue >> 4) for red, green, blue in pixels)
    saturations: list[float] = []
    values: list[float] = []
    for red, green, blue in pixels:
        _hue, saturation, value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
        saturations.append(saturation)
        values.append(value)
    total = float(len(pixels))
    return {
        "opaque": len(pixels),
        "quantised": [colour for colour, _count in quantised.most_common(limit)],
        "grey_ratio": sum(1 for item in saturations if item < 0.22) / total,
        "dark_ratio": sum(1 for item in values if item < 0.22) / total,
        "mean_saturation": sum(saturations) / total,
    }


# A drifted member costs one extra generation pass. Capping it keeps a bad set
# from turning into an unbounded retry loop.

def sprite_palette_ramp(sprite_path: str | Path, count: int = 8) -> list[tuple[int, int, int]]:
    """An ordered dark-to-light ramp taken from a rendered anchor sprite."""
    with Image.open(sprite_path) as loaded:
        image = loaded.convert("RGBA")
    counts = Counter(
        (red, green, blue)
        for red, green, blue, alpha in image.get_flattened_data()
        if alpha >= 8
    )
    if not counts:
        return []
    ordered = sorted((colour for colour, _n in counts.most_common(24)), key=lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])
    if len(ordered) <= count:
        return ordered
    return [
        ordered[round(index * (len(ordered) - 1) / float(max(count - 1, 1)))]
        for index in range(count)
    ]

def family_consistency(
    anchor_sprite: str | Path,
    member_sprites: list[str | Path],
    limit: int = 12,
    minimum_overlap: float = 0.35,
    maximum_grey_delta: float = 0.25,
) -> dict[str, Any]:
    """Measure how closely each sibling actually matches the anchor's palette.

    This is measured evidence rather than a model opinion, and it reports both
    numbers it used so a low verdict can be audited: a quantised palette
    intersection, and how far the desaturated share drifted. A sibling that
    quietly turned from wood into grey iron trips the second one even when its
    colour histogram still overlaps.
    """
    anchor = sprite_palette_profile(anchor_sprite, limit)
    anchor_colours = set(anchor["quantised"])
    rows: list[dict[str, Any]] = []
    for path in member_sprites:
        profile = sprite_palette_profile(path, limit)
        colours = set(profile["quantised"])
        union = anchor_colours | colours
        overlap = len(anchor_colours & colours) / float(len(union)) if union else 0.0
        grey_delta = abs(anchor["grey_ratio"] - profile["grey_ratio"])
        rows.append({
            "sprite": str(path),
            "palette_overlap": round(overlap, 4),
            "grey_ratio": round(profile["grey_ratio"], 4),
            "grey_ratio_delta": round(grey_delta, 4),
            "consistent": overlap >= minimum_overlap and grey_delta <= maximum_grey_delta,
        })
    return {
        "anchor": str(anchor_sprite),
        "anchor_grey_ratio": round(anchor["grey_ratio"], 4),
        "members": rows,
        "minimum_overlap": minimum_overlap,
        "maximum_grey_delta": maximum_grey_delta,
    }

def _frame_pixels(path: str | Path) -> dict[tuple[int, int], tuple[int, int, int, int]] | None:
    try:
        with Image.open(path) as loaded:
            image = loaded.convert("RGBA")
    except (OSError, ValueError):
        return None
    return {
        (x, y): image.getpixel((x, y))
        for y in range(image.height)
        for x in range(image.width)
    }



# One logical name can own a great many near-identical frames -- vanilla
# clock has 64 and compass 32. Attaching every one of them spends the whole
# vision budget on one object, so a selection expands to at most this many
# states: the frame this request is about, plus an even spread of the rest.

def frame_continuity(
    sprites: list[str | Path],
    *,
    baseline_sprites: list[str | Path] | None = None,
    minimum_shared: float = 0.35,
    tolerance: float = 0.15,
    colour_tolerance: int = 24,
) -> dict[str, Any]:
    """Measure whether several frames read as one object in different states.

    A palette intersection cannot answer this: two frames of the same draw
    animation share a palette even when the drawing underneath is unrelated.
    What the eye checks is pixel agreement on the body the frames have in
    common, so that is what is measured here -- restricted to pairs whose
    silhouettes genuinely overlap, because the members of a plain set
    (helmet, chestplate, leggings) share no body at all.

    The verdict is relative on purpose. Even the vanilla bow family only
    agrees on 29%-66% of its union pairwise: the string moves, the limb bends
    a little, and thin shapes make every moved pixel visible. Comparing a
    generated family against that same-family baseline is the only honest
    question; an absolute threshold would fail the source art.

    Two numbers are reported because they fail differently. Exact agreement
    asks whether the same pixel was painted the same way. Near agreement
    (within colour_tolerance per channel) asks whether the frames at least
    speak one colour language. A family that shares a palette but moves its
    highlights scores low on the first and high on the second; a family
    whose members drifted into different palettes scores low on both, which
    is the difference a single number would hide.
    """
    loaded = [(Path(path), _frame_pixels(path)) for path in sprites]
    loaded = [(path, pixels) for path, pixels in loaded if pixels is not None]
    rows: list[dict[str, Any]] = []
    for index in range(len(loaded)):
        for other in range(index + 1, len(loaded)):
            path_a, pixels_a = loaded[index]
            path_b, pixels_b = loaded[other]
            if len(pixels_a) != len(pixels_b):
                continue
            opaque_a = {point for point, value in pixels_a.items() if value[3] >= 8}
            opaque_b = {point for point, value in pixels_b.items() if value[3] >= 8}
            union = opaque_a | opaque_b
            if not union:
                continue
            iou = len(opaque_a & opaque_b) / float(len(union))
            if iou < minimum_shared:
                continue
            exact = 0
            near = 0
            delta_total = 0
            for point in union:
                left, right = pixels_a[point], pixels_b[point]
                if left == right:
                    exact += 1
                delta = max(abs(left[index] - right[index]) for index in range(4))
                delta_total += delta
                if delta <= colour_tolerance:
                    near += 1
            rows.append({
                "a": str(path_a),
                "b": str(path_b),
                "silhouette_iou": round(iou, 4),
                "agreement": round(exact / float(len(union)), 4),
                "near_agreement": round(near / float(len(union)), 4),
                "mean_colour_delta": round(delta_total / float(len(union)), 2),
            })
    measured = [row["agreement"] for row in rows]
    baseline: dict[str, Any] | None = None
    if baseline_sprites:
        baseline = frame_continuity(
            list(baseline_sprites),
            minimum_shared=minimum_shared,
            tolerance=tolerance,
            colour_tolerance=colour_tolerance,
        )
        baseline = baseline if baseline["pairs"] else None
    if not measured:
        verdict: bool | None = None
    elif baseline is None:
        # Nothing to compare against: report the number, claim nothing.
        verdict = None
    else:
        verdict = min(measured) >= baseline["minimum_agreement"] - tolerance
    near_measured = [row["near_agreement"] for row in rows]
    return {
        "pairs": rows,
        "pair_count": len(rows),
        "mean_agreement": round(sum(measured) / len(measured), 4) if measured else None,
        "minimum_agreement": round(min(measured), 4) if measured else None,
        "mean_near_agreement": (
            round(sum(near_measured) / len(near_measured), 4) if near_measured else None
        ),
        "minimum_near_agreement": round(min(near_measured), 4) if near_measured else None,
        "colour_tolerance": colour_tolerance,
        "baseline": (
            None
            if baseline is None
            else {
                "minimum_agreement": baseline["minimum_agreement"],
                "mean_agreement": baseline["mean_agreement"],
                "minimum_near_agreement": baseline["minimum_near_agreement"],
                "mean_near_agreement": baseline["mean_near_agreement"],
                "pair_count": baseline["pair_count"],
            }
        ),
        "tolerance": tolerance,
        "consistent": verdict,
    }
