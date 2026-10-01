"""Deterministic art-quality evidence: value bands, accents, structure, families.

The failures this module exists to make measurable are the ones a human keeps
having to point at by hand:

* a stone or an ingot rendered as equal-value scatter instead of a value ramp;
* "two jarring pixels" -- an accent that lands as an isolated speck rather than
  a readable deposit, and an accent whose edge against its base is a hard step;
* a family whose members share a palette histogram but not a hue or value axis,
  so nobody can tell they are the same object.

There are two kinds of gate here, and the split matters. **Violation gates** ask
"did you break a rule": too many accent pixels, a cluster too small, an edge too
hard. **Structure gates** ask "does this read as drawn rather than stamped": is
one deposit shape pasted repeatedly, are the deposits evenly spaced, does a
deposit actually use the value ramp it declared. A sprite can satisfy every
violation gate and still be the thing a person rejects -- four identical 8-pixel
stamps on a grid pass a budget, a minimum cluster size and an edge test -- which
is why both kinds exist.

Every number here is plain colour statistics over the rendered PNG. No model,
no object vocabulary, no judgement about what the object *is* -- that stays with
the caller. The engine only answers "what did this raster actually come out
like", and asserts the budgets the caller declared in the plan.
"""

from __future__ import annotations

import colorsys
import re
from collections import Counter, deque
from math import atan2, cos, pi, sin
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image


_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")

# One quantisation step for "distinct value band" counting. Eight luma levels
# per band keeps two authentic vanilla stone shades apart while collapsing the
# 1-2 level dither a re-render introduces.
_DEFAULT_BAND_STEP = 8.0

# A pixel counts as an isolated speck when every in-mask neighbour differs from
# it by more than this. 24 is above a normal two-step dither and below the
# amber-on-stone gap a "jarring dot" is made of.
_DEFAULT_FLAT_TOLERANCE = 24.0

# Hue distance at which a saturated pixel stops belonging to the asset's own
# hue and starts reading as an accent. 0.055 of the wheel is about 20 degrees.
_DEFAULT_ACCENT_HUE_DISTANCE = 0.055
# A chromatic outlier must clear this chroma before it can be an accent at all.
# Desaturated metal, grey stone and cold blue-grey all stay base.
_DEFAULT_ACCENT_CHROMA = 28.0


# ---------------------------------------------------------------------------
# small colour helpers, deliberately independent of the renderer
# ---------------------------------------------------------------------------


def hex_to_rgb(token: object) -> tuple[int, int, int] | None:
    """Resolve ``#rrggbb`` (or ``#rgb``) to a colour, or None."""
    text = str(token).strip()
    if not text:
        return None
    if not text.startswith("#"):
        return None
    text = text[1:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def resolve_colour(value: object, palette: dict[str, str] | None = None) -> tuple[int, int, int] | None:
    """Accept a literal hex colour or a palette token name."""
    direct = hex_to_rgb(value)
    if direct is not None:
        return direct
    if palette:
        return hex_to_rgb(palette.get(str(value)))
    return None


def luma(colour: tuple[int, int, int]) -> float:
    return 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]


def chroma(colour: tuple[int, int, int]) -> float:
    return float(max(colour) - min(colour))


def hsv(colour: tuple[int, int, int]) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0)


def hue_distance(first: float, second: float) -> float:
    """Shortest distance on the hue wheel, in turns."""
    distance = abs(first - second) % 1.0
    return min(distance, 1.0 - distance)


def _chebyshev(first: tuple[int, int, int], second: tuple[int, int, int]) -> int:
    return max(abs(first[index] - second[index]) for index in range(3))


def _circular_mean(turns: Iterable[float], weights: Sequence[float] | None = None) -> float | None:
    """Weighted mean direction on the hue wheel, ``None`` when undefined."""
    total_x = total_y = total_weight = 0.0
    for index, turn in enumerate(turns):
        weight = 1.0 if weights is None else weights[index]
        if weight <= 0:
            continue
        angle = turn * 2.0 * pi
        total_x += cos(angle) * weight
        total_y += sin(angle) * weight
        total_weight += weight
    if total_weight <= 0 or (abs(total_x) < 1e-12 and abs(total_y) < 1e-12):
        return None
    angle = atan2(total_y, total_x) / (2.0 * pi)
    return angle % 1.0


def circular_span(turns: Sequence[float]) -> float:
    """Largest pairwise arc between directions on the hue wheel, in turns."""
    if len(turns) < 2:
        return 0.0
    return max(
        hue_distance(first, second)
        for index, first in enumerate(turns)
        for second in turns[index + 1:]
    )


# ---------------------------------------------------------------------------
# reading a sprite
# ---------------------------------------------------------------------------


def _load(image: "Image.Image | str | Path") -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGBA")
    with Image.open(image) as loaded:
        return loaded.convert("RGBA")


def _opaque_map(image: Image.Image) -> dict[tuple[int, int], tuple[int, int, int]]:
    return {
        (x, y): image.getpixel((x, y))[:3]
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y))[3] >= 8
    }


_NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def components(points: set[tuple[int, int]], diagonal: bool = True) -> list[set[tuple[int, int]]]:
    """8-connected (default) or 4-connected components of a pixel set."""
    steps = list(_NEIGHBOURS)
    if diagonal:
        steps += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
    unseen = set(points)
    found: list[set[tuple[int, int]]] = []
    while unseen:
        start = unseen.pop()
        group = {start}
        queue = deque([start])
        while queue:
            x, y = queue.popleft()
            for dx, dy in steps:
                neighbour = (x + dx, y + dy)
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    group.add(neighbour)
                    queue.append(neighbour)
        found.append(group)
    return found


# ---------------------------------------------------------------------------
# value bands: a ramp, not scatter
# ---------------------------------------------------------------------------


def band_report(
    image: "Image.Image | str | Path",
    *,
    band_step: float = _DEFAULT_BAND_STEP,
    flat_tolerance: float = _DEFAULT_FLAT_TOLERANCE,
    maximum_isolated: float | None = None,
    maximum_step: float | None = None,
) -> dict[str, Any]:
    """Describe the value structure of a sprite, and whether it reads as scatter.

    Two numbers, because "not a ramp" fails in two different ways:

    * ``isolated_share`` -- the share of opaque pixels whose *every* in-mask
      orthogonal neighbour is more than ``flat_tolerance`` luma away. This is
      the literal "突兀的来一两个点": a handful of dots on a flat fill. A pixel
      with no in-mask neighbour (a one-pixel antenna) is not counted, because it
      has no neighbour to disagree with. Gate it with ``maximum_isolated``.
    * ``mean_neighbour_step`` -- the average luma jump between adjacent pixels.
      A ramp or a banded ramp keeps neighbours close, so the step is small; a
      sprite whose every pixel is decided at random has a step near half its
      value range, and barely any pixel is isolated from *all four* neighbours.
      Gate it with ``maximum_step``.
    """
    loaded = _load(image)
    pixels = _opaque_map(loaded)
    if not pixels:
        return {
            "opaque_pixels": 0,
            "bands": [],
            "band_count": 0,
            "isolated_pixels": 0,
            "isolated_share": 0.0,
            "mean_neighbour_step": 0.0,
            "largest_flat_share": 0.0,
            "verdict": "empty",
            "maximum_isolated": maximum_isolated,
            "maximum_step": maximum_step,
            "consistent": None,
            "reasons": [],
        }
    values = {point: luma(colour) for point, colour in pixels.items()}
    bands = Counter(int(value // band_step) for value in values.values())
    isolated = 0
    steps: list[float] = []
    for point, value in values.items():
        x, y = point
        neighbours = [
            values[(x + dx, y + dy)]
            for dx, dy in _NEIGHBOURS
            if (x + dx, y + dy) in values
        ]
        if not neighbours:
            continue
        gaps = [abs(value - neighbour) for neighbour in neighbours]
        steps.extend(gaps)
        if all(gap > flat_tolerance for gap in gaps):
            isolated += 1
    total = len(values)
    flat_share = Counter(pixels.values()).most_common(1)[0][1] / float(total)
    ordered_bands = sorted(bands)
    report: dict[str, Any] = {
        "opaque_pixels": total,
        "bands": ordered_bands,
        "band_count": len(ordered_bands),
        "band_step": band_step,
        "isolated_pixels": isolated,
        "isolated_share": round(isolated / float(total), 4),
        "mean_neighbour_step": round(sum(steps) / float(len(steps)), 3) if steps else 0.0,
        "largest_flat_share": round(flat_share, 4),
        "flat_tolerance": flat_tolerance,
        "maximum_isolated": maximum_isolated,
        "maximum_step": maximum_step,
    }
    reasons: list[str] = []
    if maximum_isolated is not None and report["isolated_share"] > maximum_isolated:
        reasons.append(
            "%.2f%% of opaque pixels are isolated from every neighbour by more than %.0f luma (limit %.2f%%)"
            % (report["isolated_share"] * 100.0, flat_tolerance, maximum_isolated * 100.0)
        )
    if maximum_step is not None and report["mean_neighbour_step"] > maximum_step:
        reasons.append(
            "mean neighbour step %.1f exceeds %.1f: adjacent pixels disagree too much for a ramp"
            % (report["mean_neighbour_step"], maximum_step)
        )
    report["reasons"] = reasons
    if maximum_isolated is None and maximum_step is None:
        report["verdict"] = "unchecked"
        report["consistent"] = None
    else:
        report["consistent"] = not reasons
        report["verdict"] = "banded" if report["consistent"] else "scattered"
    return report


# ---------------------------------------------------------------------------
# accent budget
# ---------------------------------------------------------------------------


def _base_hue(pixels: dict[tuple[int, int], tuple[int, int, int]]) -> float | None:
    """The hue a sprite is mostly about, ignoring its desaturated pixels."""
    turns: list[float] = []
    weights: list[float] = []
    for colour in pixels.values():
        hue, saturation, value = hsv(colour)
        if chroma(colour) < 12.0:
            continue
        turns.append(hue)
        weights.append(chroma(colour) * (0.25 + value))
    return _circular_mean(turns, weights)


def accent_points(
    image: "Image.Image | str | Path",
    *,
    accent_colors: Sequence[object] = (),
    base_colors: Sequence[object] = (),
    palette: dict[str, str] | None = None,
    accent_tolerance: int = 48,
    accent_chroma: float = _DEFAULT_ACCENT_CHROMA,
    hue_distance_limit: float = _DEFAULT_ACCENT_HUE_DISTANCE,
) -> tuple[set[tuple[int, int]], str]:
    """The pixels that read as the sprite's accent colour, and how they were found.

    ``declared`` is the honest mode: the plan named its accent swatches, so a
    pixel is an accent when it sits within ``accent_tolerance`` per channel of
    one of them and is not nearer to a declared base swatch. ``inferred`` is the
    fallback for a plan that declared nothing: a pixel is an accent when it is
    clearly more chromatic than the sprite's own material and its hue is more
    than ``hue_distance_limit`` away from the sprite's base hue. A plain stone
    or a plain ingot scores zero either way, which is the point.
    """
    loaded = _load(image)
    pixels = _opaque_map(loaded)
    if not pixels:
        return set(), "empty"
    resolved_accent = [
        colour for colour in (resolve_colour(value, palette) for value in accent_colors)
        if colour is not None
    ]
    resolved_base = [
        colour for colour in (resolve_colour(value, palette) for value in base_colors)
        if colour is not None
    ]
    if resolved_accent:
        found: set[tuple[int, int]] = set()
        for point, colour in pixels.items():
            nearest = min(_chebyshev(colour, accent) for accent in resolved_accent)
            if nearest > accent_tolerance:
                continue
            if resolved_base and min(
                _chebyshev(colour, base) for base in resolved_base
            ) < nearest:
                continue
            found.add(point)
        return found, "declared"

    base_hue = _base_hue(pixels)
    chromatic = [chroma(colour) for colour in pixels.values()]
    ordered = sorted(chromatic)
    median_chroma = ordered[len(ordered) // 2]
    threshold = max(accent_chroma, median_chroma + 18.0)
    found = set()
    for point, colour in pixels.items():
        if chroma(colour) < threshold:
            continue
        if base_hue is None:
            found.add(point)
            continue
        if hue_distance(hsv(colour)[0], base_hue) > hue_distance_limit:
            found.add(point)
    return found, "inferred"


def make_accent_mask(
    image: "Image.Image | str | Path",
    points: set[tuple[int, int]],
) -> Image.Image:
    """A 1-bit mask of the accent pixels of ``image`` (255 = accent)."""
    loaded = _load(image)
    mask = Image.new("L", loaded.size, 0)
    for x, y in points:
        if 0 <= x < loaded.width and 0 <= y < loaded.height:
            mask.putpixel((x, y), 255)
    return mask


def despeckle_accent(
    image: "Image.Image | str | Path",
    points: set[tuple[int, int]],
    minimum_cluster: int,
) -> tuple[Image.Image, set[tuple[int, int]], int]:
    """Repaint accent clusters smaller than ``minimum_cluster`` with base colour.

    This is the deterministic half of "不能是这么难看的点点": a declared
    minimum deposit size means a two-pixel accident cannot survive to the
    delivered PNG, while a deliberately authored cluster is untouched. The
    replacement colour is borrowed from the nearest non-accent opaque pixel, so
    the base material keeps its own grain instead of being filled flat.
    """
    loaded = _load(image).copy()
    if minimum_cluster <= 1 or not points:
        return loaded, set(points), 0
    removed: set[tuple[int, int]] = set()
    for group in components(points):
        if len(group) >= minimum_cluster:
            continue
        removed |= group
    if not removed:
        return loaded, set(points), 0
    opaque = {
        (x, y): loaded.getpixel((x, y))[:3]
        for y in range(loaded.height)
        for x in range(loaded.width)
        if loaded.getpixel((x, y))[3] >= 8
    }
    donors = [point for point in opaque if point not in removed and point not in points]
    if not donors:
        donors = [point for point in opaque if point not in removed]
    if not donors:
        return loaded, set(points) - removed, len(removed)
    # One breadth-first sweep from the whole donor frontier, so a removed pixel
    # always takes the value of the base pixel physically nearest to it.
    distance: dict[tuple[int, int], tuple[int, int]] = {}
    frontier = deque()
    for donor in donors:
        distance[donor] = donor
        frontier.append(donor)
    while frontier:
        x, y = frontier.popleft()
        source = distance[(x, y)]
        for dx, dy in _NEIGHBOURS:
            neighbour = (x + dx, y + dy)
            if neighbour in removed and neighbour not in distance:
                # Walk out from the donor, so the nearest donor wins.
                distance[neighbour] = source
                frontier.append(neighbour)
    for point in sorted(removed):
        x, y = point
        if point not in distance:
            continue
        red, green, blue = opaque[distance[point]]
        loaded.putpixel((x, y), (red, green, blue, loaded.getpixel((x, y))[3]))
    return loaded, set(points) - removed, len(removed)


def _motif_shape(cluster: set[tuple[int, int]]) -> str:
    """A cluster's shape with its size and position removed.

    Crop to the bounding box and emit one character per cell, so two deposits
    drawn from the same stamp produce the same string wherever they sit and
    whatever shade each pixel took. That identity is the whole point: a budget
    counts four deposits as four, and only this sees that they are one stamp.
    """
    xs = [point[0] for point in cluster]
    ys = [point[1] for point in cluster]
    left, top = min(xs), min(ys)
    width, height = max(xs) - left + 1, max(ys) - top + 1
    grid = [["." for _ in range(width)] for _ in range(height)]
    for x, y in cluster:
        grid[y - top][x - left] = "#"
    return "/".join("".join(row) for row in grid)


def _principal_axis(points: Sequence[tuple[int, int]]) -> tuple[float, float]:
    """Major-axis direction of a small pixel set, as (cos, sin), plus spread."""
    count = len(points)
    if count < 2:
        return 1.0, 0.0
    mean_x = sum(point[0] for point in points) / count
    mean_y = sum(point[1] for point in points) / count
    xx = yy = xy = 0.0
    for x, y in points:
        dx, dy = x - mean_x, y - mean_y
        xx += dx * dx
        yy += dy * dy
        xy += dx * dy
    angle = 0.5 * atan2(2.0 * xy, xx - yy)
    return cos(angle), sin(angle)


def _coefficient_of_variation(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 1e-9:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return (variance ** 0.5) / mean


def _centroid(cluster: set[tuple[int, int]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in cluster) / len(cluster),
        sum(point[1] for point in cluster) / len(cluster),
    )


def _mirror_score(centroids: Sequence[tuple[float, float]]) -> float:
    """How well the centroid set maps onto itself under a mirror, 0..1.

    Reported rather than gated: one asset may legitimately be symmetric, and a
    symmetry test alone would call a deliberately symmetric emblem "stamped".
    The layout verdict below uses spacing and scale spread instead.
    """
    if len(centroids) < 2:
        return 0.0
    width = max(point[0] for point in centroids) - min(point[0] for point in centroids)
    height = max(point[1] for point in centroids) - min(point[1] for point in centroids)
    scores: list[float] = []
    for axis in ("x", "y"):
        span = width if axis == "x" else height
        if span <= 0:
            continue
        matched = 0
        for point in centroids:
            mirrored = (
                (width - point[0], point[1]) if axis == "x" else (point[0], height - point[1])
            )
            nearest = min(
                (abs(other[0] - mirrored[0]) + abs(other[1] - mirrored[1]))
                for other in centroids
            )
            if nearest <= max(1.0, span * 0.2):
                matched += 1
        scores.append(matched / float(len(centroids)))
    return round(max(scores), 3) if scores else 0.0


def _ramp_monotone_share(
    cluster: set[tuple[int, int]],
    pixels: dict[tuple[int, int], tuple[int, int, int]],
) -> float:
    """Share of value rings around a deposit's bright core that fall off outward.

    Monotonicity along an axis was the first attempt and it was wrong: it fails a
    deposit whose brightest pixels are in the *middle*, which is precisely what a
    sheen along an edge or a lit gem looks like. The structure that both a
    directional ramp and a centre-bright streak share is **falloff from the
    core**, so that is what is measured: group the deposit's pixels into rings by
    distance from its brightest pixel(s), average each ring's value, and count
    the ring-to-ring steps that go down.

    A flat fill has one ring and scores 1.0 -- it is monotone by vacuity, and the
    dominant-share gate is the one that has something to say about it. Shades
    sprinkled at random put bright pixels in the outer rings, so the sequence
    rises and the share drops.
    """
    if len(cluster) < 3:
        return 1.0
    core_luma = max(luma(pixels[point]) for point in cluster)
    core = [point for point in cluster if luma(pixels[point]) >= core_luma - 1e-9]
    rings: dict[float, list[float]] = {}
    for point in cluster:
        distance = min(
            ((point[0] - other[0]) ** 2 + (point[1] - other[1]) ** 2) ** 0.5 for other in core
        )
        rings.setdefault(round(distance, 1), []).append(luma(pixels[point]))
    if len(rings) < 2:
        return 1.0
    means = [
        (distance, sum(values) / len(values)) for distance, values in sorted(rings.items())
    ]
    # A one-luma wobble between rings is rounding, not a rise.
    steps = [
        means[index + 1][1] - means[index][1] for index in range(len(means) - 1)
    ]
    falling = sum(1 for step in steps if step <= 2.0) / len(steps)
    return round(falling, 4)


def _bar_shape(cluster: set[tuple[int, int]], *, fill_max: float, min_aspect: float) -> dict[str, Any]:
    """Is this accent a filled, elongated rectangle -- a bar pasted on?

    The question is not "is it a rectangle". A compact 2x2 fleck, a diamond, an
    L-shaped deposit: all fine, and all are what a deposit looks like when it is
    part of the thing it sits on. What is not fine is an accent whose outline has
    nothing to do with the form under it -- a filled band. So the test needs
    **both** a high bounding-box fill *and* elongation: a solid 3x3 has fill 1.0
    and aspect 1.0 and is not a bar; a solid 7x3 has fill 0.81 and aspect 2.33
    and is.
    """
    xs = [point[0] for point in cluster]
    ys = [point[1] for point in cluster]
    width = max(xs) - min(xs) + 1
    height = max(ys) - min(ys) + 1
    fill = len(cluster) / float(width * height)
    aspect = max(width, height) / float(min(width, height))
    return {
        "size": len(cluster),
        "bbox": [min(xs), min(ys), width, height],
        "fill_ratio": round(fill, 4),
        "aspect": round(aspect, 2),
        "bar": fill >= fill_max and aspect >= min_aspect,
    }


def accent_structure_report(
    points: set[tuple[int, int]],
    pixels: dict[tuple[int, int], tuple[int, int, int]],
    *,
    motif_repeat_max: int | None = 2,
    layout_min_size_cv: float | None = 0.15,
    layout_min_spacing_cv: float | None = 0.15,
    ramp_min_pixels: int | None = 6,
    ramp_min_levels: int | None = 3,
    ramp_max_dominant_share: float | None = 0.6,
    ramp_min_monotone: float | None = 0.6,
    bar_fill_max: float | None = 0.75,
    bar_min_aspect: float | None = 1.8,
    bar_min_pixels: int | None = 10,
    ramp_level_step: float = 16.0,
) -> tuple[dict[str, Any], list[str]]:
    """Ask whether the accents look drawn rather than stamped.

    Three independent questions, each with the fault it was written for:

    * **motif repeat** -- the same deposit shape pasted around the face. Four
      identical 8-pixel crosses are four legal deposits to a budget.
    * **layout** -- every deposit the same size and the same distance apart, so
      the face reads as a pattern. Needs both, not either: a real ore often has
      deposits of similar size.
    * **ramp use** -- a large deposit that spends its pixels on one shade. A
      declared four-stop ramp that the raster uses as one dark blob is the
      "jarring colour block" fault.

    Returns the report and the list of failure reasons.
    """
    clusters = components(points)
    sizes = [len(cluster) for cluster in clusters]
    report: dict[str, Any] = {"clusters": len(clusters)}
    reasons: list[str] = []

    # -- motivation: is one shape pasted repeatedly? -------------------------
    shapes = Counter(_motif_shape(cluster) for cluster in clusters)
    repeat = max(shapes.values()) if shapes else 0
    motif_ok = motif_repeat_max is None or repeat <= motif_repeat_max
    report["motif"] = {
        "distinct_shapes": len(shapes),
        "repeat_max": repeat,
        "repeat_max_allowed": motif_repeat_max,
        "clusters": len(clusters),
        "ok": motif_ok,
    }
    if not motif_ok:
        reasons.append(
            "accent motif repeat: one deposit shape appears %d times (limit %d); "
            "vary the deposit shapes instead of stamping one"
            % (repeat, motif_repeat_max)
        )

    # -- layout: all the same size, all the same distance apart? -------------
    centroids = [_centroid(cluster) for cluster in clusters]
    size_cv = _coefficient_of_variation([float(size) for size in sizes])
    spacing = []
    for index, centroid in enumerate(centroids):
        others = [
            ((centroid[0] - other[0]) ** 2 + (centroid[1] - other[1]) ** 2) ** 0.5
            for other_index, other in enumerate(centroids)
            if other_index != index
        ]
        if others:
            spacing.append(min(others))
    spacing_cv = _coefficient_of_variation(spacing)
    quadrant_occupancy = len({
        (0 if centroid[0] < 8 else 1, 0 if centroid[1] < 8 else 1)
        for centroid in centroids
    })
    # A layout verdict needs at least three deposits: with two, "evenly spaced"
    # and "same size" are not statements about anything.
    layout_checked = len(clusters) >= 3
    layout_ok = True
    if layout_checked and layout_min_size_cv is not None and layout_min_spacing_cv is not None:
        layout_ok = not (size_cv < layout_min_size_cv and spacing_cv < layout_min_spacing_cv)
    report["layout"] = {
        "size_cv": round(size_cv, 4),
        "spacing_cv": round(spacing_cv, 4),
        "quadrant_occupancy": quadrant_occupancy,
        "mirror_score": _mirror_score(centroids),
        "minimum_size_cv": layout_min_size_cv,
        "minimum_spacing_cv": layout_min_spacing_cv,
        "checked": layout_checked,
        "ok": layout_ok,
    }
    if not layout_ok:
        reasons.append(
            "accent layout regularity: %d deposits with size spread %.2f and spacing spread %.2f "
            "(both below %.2f) read as a stamped pattern; vary the deposit sizes and the gaps"
            % (len(clusters), size_cv, spacing_cv, layout_min_size_cv or 0.0)
        )

    # -- ramp use: does a large deposit actually spend its ramp? -------------
    # Only the worst offender per rule is quoted: the same mistake made by four
    # deposits is one fault, and a report that repeats it four times is harder
    # to read than the fault is to fix.
    levels_used: list[int] = []
    dominant_shares: list[float] = []
    monotone_shares: list[float] = []
    ramp_ok = True
    worst_levels: tuple[int, int] | None = None       # (levels, cluster size)
    worst_dominant: tuple[float, int] | None = None   # (share, cluster size)
    worst_monotone: tuple[float, int] | None = None   # (share, cluster size)
    for cluster in clusters:
        if ramp_min_pixels is not None and len(cluster) < ramp_min_pixels:
            continue
        bins = Counter(int(luma(pixels[point]) // ramp_level_step) for point in cluster)
        levels = len(bins)
        dominant = bins.most_common(1)[0][1] / float(len(cluster))
        monotone = _ramp_monotone_share(cluster, pixels)
        levels_used.append(levels)
        dominant_shares.append(round(dominant, 4))
        monotone_shares.append(monotone)
        if worst_levels is None or levels < worst_levels[0]:
            worst_levels = (levels, len(cluster))
        if worst_dominant is None or dominant > worst_dominant[0]:
            worst_dominant = (dominant, len(cluster))
        if worst_monotone is None or monotone < worst_monotone[0]:
            worst_monotone = (monotone, len(cluster))
    if ramp_min_levels is not None and worst_levels and worst_levels[0] < ramp_min_levels:
        ramp_ok = False
        reasons.append(
            "accent ramp use: a %d-pixel deposit uses only %d value level(s) of the declared %d; "
            "give each deposit a light core and a darker rim"
            % (worst_levels[1], worst_levels[0], ramp_min_levels)
        )
    if (
        ramp_max_dominant_share is not None
        and worst_dominant
        and worst_dominant[0] > ramp_max_dominant_share
    ):
        ramp_ok = False
        reasons.append(
            "accent ramp use: %.0f%% of a %d-pixel deposit sits in one value level (limit %.0f%%); "
            "the ramp was declared but the deposit is mostly a single flat shade"
            % (worst_dominant[0] * 100.0, worst_dominant[1], ramp_max_dominant_share * 100.0)
        )
    if ramp_min_monotone is not None and worst_monotone and worst_monotone[0] < ramp_min_monotone:
        ramp_ok = False
        reasons.append(
            "accent ramp use: only %.0f%% of value steps inside a %d-pixel deposit run the same way "
            "(limit %.0f%%); the shades are sprinkled rather than shaded"
            % (worst_monotone[0] * 100.0, worst_monotone[1], ramp_min_monotone * 100.0)
        )
    report["ramp"] = {
        "checked_clusters": len(levels_used),
        "levels_used": levels_used,
        "dominant_shares": dominant_shares,
        "monotone_shares": monotone_shares,
        "minimum_pixels": ramp_min_pixels,
        "minimum_levels": ramp_min_levels,
        "maximum_dominant_share": ramp_max_dominant_share,
        "minimum_monotone": ramp_min_monotone,
        "ok": ramp_ok,
    }

    # -- shape: is the accent a bar pasted on? ------------------------------
    bars: list[dict[str, Any]] = []
    if bar_fill_max is not None and bar_min_aspect is not None:
        for cluster in clusters:
            if bar_min_pixels is not None and len(cluster) < bar_min_pixels:
                continue
            shape = _bar_shape(cluster, fill_max=bar_fill_max, min_aspect=bar_min_aspect)
            if shape["bar"]:
                bars.append(shape)
    report["shape"] = {
        "bar_clusters": len(bars),
        "bars": bars,
        "fill_max": bar_fill_max,
        "min_aspect": bar_min_aspect,
        "min_pixels": bar_min_pixels,
        "ok": not bars,
    }
    for shape in bars:
        reasons.append(
            "accent shape is a bar: a %d-pixel accent fills %.0f%% of its %dx%d bounding box "
            "(limit %.0f%%) and is %.1f:1 elongated; the highlight's outline has nothing to do "
            "with the form it lies on, so it reads as pasted on. Take the accent from the "
            "reference's own lighting instead of drawing a band"
            % (
                shape["size"], shape["fill_ratio"] * 100.0,
                shape["bbox"][2], shape["bbox"][3],
                bar_fill_max * 100.0, shape["aspect"],
            )
        )

    report["ok"] = bool(motif_ok and layout_ok and ramp_ok and not bars)
    return report, reasons


def accent_audit(
    image: "Image.Image | str | Path",
    *,
    accent_colors: Sequence[object] = (),
    base_colors: Sequence[object] = (),
    palette: dict[str, str] | None = None,
    budget: int | None = None,
    minimum_cluster: int = 1,
    edge_max: float | None = None,
    accent_tolerance: int = 48,
    motif_repeat_max: int | None = 2,
    layout_min_size_cv: float | None = 0.15,
    layout_min_spacing_cv: float | None = 0.15,
    ramp_min_pixels: int | None = 6,
    ramp_min_levels: int | None = 3,
    ramp_max_dominant_share: float | None = 0.6,
    ramp_min_monotone: float | None = 0.6,
    bar_fill_max: float | None = 0.75,
    bar_min_aspect: float | None = 1.8,
    bar_min_pixels: int | None = 10,
    accent_base_gap_max: float | None = 20.0,
    accent_base_edge_mean_max: float | None = 35.0,
    threshold_waiver: str = "",
    points: set[tuple[int, int]] | None = None,
) -> dict[str, Any]:
    """Count the accent pixels a sprite spent, and check every declared budget.

    Six independent questions, in two families, because they fail differently.

    Violation gates -- did a limit get broken?

    * ``budget`` -- how many accent pixels did the sprite use? A plain block
      declares 0 and must come out at 0.
    * ``minimum_cluster`` -- is any accent a lone speck rather than a deposit?
    * ``edge_max`` -- how hard is the step from an accent pixel to the base
      material next to it? This is the number behind "橙色和蓝色的边缘要拖突兀有多突兀".

    Structure gates -- does it read as drawn rather than stamped? See
    :func:`accent_structure_report`; ``motif_repeat_max``, the two layout spreads
    and the three ramp thresholds are passed straight through. A sprite can pass
    every violation gate and still be the thing a person rejects.
    """
    loaded = _load(image)
    pixels = _opaque_map(loaded)
    # When the renderer supplies the accent set it decided, use it verbatim: the
    # engine knows which pixels it placed, and a distance test against the
    # declared swatches loses them the moment they are embedded.
    if points is not None:
        points = {point for point in points if point in pixels}
        mode = "renderer_declared"
    else:
        points, mode = accent_points(
            loaded,
            accent_colors=accent_colors,
            base_colors=base_colors,
            palette=palette,
            accent_tolerance=accent_tolerance,
        )
    base_points = set(pixels) - points
    structure, structure_reasons = accent_structure_report(
        points,
        pixels,
        motif_repeat_max=motif_repeat_max,
        layout_min_size_cv=layout_min_size_cv,
        layout_min_spacing_cv=layout_min_spacing_cv,
        ramp_min_pixels=ramp_min_pixels,
        ramp_min_levels=ramp_min_levels,
        ramp_max_dominant_share=ramp_max_dominant_share,
        ramp_min_monotone=ramp_min_monotone,
        bar_fill_max=bar_fill_max,
        bar_min_aspect=bar_min_aspect,
        bar_min_pixels=bar_min_pixels,
    )
    groups = components(points)
    sizes = sorted((len(group) for group in groups), reverse=True)
    undersized = [size for size in sizes if size < minimum_cluster]
    # The step that reads as "jarring" at an accent boundary is a brightness
    # step: a hue/chroma difference is what makes something an accent at all,
    # so counting it would rate every accent as harsh. How many luma levels the
    # accent jumps by where it meets the material is the number a person means
    # by "要拖突兀有多突兀".
    edges: list[float] = []
    edge_chromas: list[float] = []
    for x, y in points:
        candidates = [
            (x + dx, y + dy)
            for dx, dy in _NEIGHBOURS
            if (x + dx, y + dy) in pixels and (x + dx, y + dy) not in points
        ]
        if not candidates:
            continue
        pixel = pixels[(x, y)]
        nearest = min(
            candidates,
            key=lambda point: abs(luma(pixel) - luma(pixels[point])),
        )
        edges.append(abs(luma(pixel) - luma(pixels[nearest])))
        edge_chromas.append(abs(chroma(pixel) - chroma(pixels[nearest])))
    edges.sort()
    percentile_90 = edges[min(len(edges) - 1, int(len(edges) * 0.9))] if edges else 0.0
    # How far the accents sit from the material they are set into. Vanilla's own
    # iron_ore measures 12.2 here against 23.3 edge-mean and 43.4 edge-p90 -- its
    # flecks are nearly dissolved in their stone. A build that pasted those same
    # flecks onto a darker stone measured 42.7 against 55.7/92.3, and the flecks
    # read as stuck on even though every pixel of them was vanilla's: the
    # relationship to the base is what "embedded" means, not the fleck.
    base_lumas = [luma(pixels[point]) for point in base_points]
    base_luma_mean = sum(base_lumas) / len(base_lumas) if base_lumas else 0.0
    accent_luma_mean = (
        sum(luma(pixels[point]) for point in points) / len(points) if points else 0.0
    )
    report: dict[str, Any] = {
        "sprite": str(image) if not isinstance(image, Image.Image) else None,
        "opaque_pixels": len(pixels),
        "detection": mode,
        "accent_colors": [str(value) for value in accent_colors],
        "base_colors": [str(value) for value in base_colors],
        "accent_pixels": len(points),
        "accent_share": round(len(points) / float(max(len(pixels), 1)), 4),
        "clusters": len(groups),
        "largest_cluster": sizes[0] if sizes else 0,
        "smallest_cluster": sizes[-1] if sizes else 0,
        "cluster_sizes": sizes[:12],
        "below_minimum_clusters": len(undersized),
        "below_minimum_pixels": sum(undersized),
        "minimum_cluster": minimum_cluster,
        "edge_delta_mean": round(sum(edges) / float(len(edges)), 2) if edges else None,
        "edge_delta_p90": round(percentile_90, 2) if edges else None,
        "accent_base_gap_mean": round(abs(accent_luma_mean - base_luma_mean), 2) if points else None,
        "accent_luma_mean": round(accent_luma_mean, 2) if points else None,
        "base_luma_mean": round(base_luma_mean, 2),
        "edge_chroma_mean": (
            round(sum(edge_chromas) / len(edge_chromas), 2) if edge_chromas else None
        ),
        "edge_delta_kind": "luma step from an accent pixel to the material beside it",
        "edge_max": edge_max,
    }
    accent_hue: float | None = None
    accent_turns: list[float] = []
    accent_weights: list[float] = []
    accent_lumas: list[float] = []
    for point in points:
        colour = pixels[point]
        accent_lumas.append(luma(colour))
        _hue, _saturation, _value = hsv(colour)
        if chroma(colour) >= 12.0:
            accent_turns.append(_hue)
            accent_weights.append(chroma(colour))
    accent_hue = _circular_mean(accent_turns, accent_weights)
    report["accent_hue_degrees"] = None if accent_hue is None else round(accent_hue * 360.0, 1)
    accent_saturations = [
        hsv(pixels[point])[1] for point in points
    ]
    report["accent_saturation_mean"] = (
        round(sum(accent_saturations) / len(accent_saturations), 4)
        if accent_saturations else None
    )
    report["accent_luma_mean"] = (
        round(sum(accent_lumas) / len(accent_lumas), 2) if accent_lumas else None
    )
    report["within_budget"] = budget is None or len(points) <= budget
    report["clusters_ok"] = sum(undersized) == 0
    report["edge_ok"] = edge_max is None or (report["edge_delta_p90"] or 0.0) <= edge_max
    # Embedded, or pasted on? Vanilla iron_ore: gap 12.2, edge mean 23.3, p90 43.4.
    #
    # Not applicable when there is no accent, or when the sprite has no base for
    # an accent to sit in -- a fixture that paints one dot on transparency has no
    # "material it is set into", and asking the question there is a category
    # error, not a failure.
    gap = report["accent_base_gap_mean"] or 0.0
    edge_mean = report["edge_delta_mean"] or 0.0
    # Not applicable when there is no accent, no base pixels under it, no declared
    # base material -- or only a single accent pixel. Embedding is a relationship
    # between a deposit and the material around it, so it needs both a boundary
    # and an interior; a lone pixel has neither and is the cluster gate's business.
    gap_applicable = (
        len(points) >= 2 and bool(base_points) and bool(base_colors)
    )
    gap_ok = (
        accent_base_gap_max is None
        or not gap_applicable
        or (gap <= accent_base_gap_max and edge_mean <= accent_base_edge_mean_max)
    )
    report["base_gap"] = {
        "applicable": gap_applicable,
        "accent_base_gap_mean": report["accent_base_gap_mean"],
        "edge_delta_mean": report["edge_delta_mean"],
        "edge_delta_p90": report["edge_delta_p90"],
        "maximum_gap": accent_base_gap_max,
        "maximum_edge_mean": accent_base_edge_mean_max,
        "ok": gap_ok,
    }
    gap_waived = False
    if not gap_ok and threshold_waiver:
        # Allowed with a stated reason, exactly as a widened limit is: the number
        # is still reported, it just does not refuse the asset. The reason text is
        # routed to `waived_reasons` so the verdict and the message cannot
        # disagree -- "passed, but here is why it did not" is how a waiver turns
        # back into a silent green.
        gap_ok = True
        gap_waived = True
        report["base_gap"]["ok"] = True
        report["base_gap"]["waived"] = threshold_waiver
    report["base_gap_ok"] = gap_ok
    report["points"] = sorted(points)
    report["budget"] = budget
    report["structure"] = structure
    report["consistent"] = bool(
        report["within_budget"]
        and report["clusters_ok"]
        and report["edge_ok"]
        and gap_ok
        and structure["ok"]
    )
    reasons: list[str] = []
    waived_reasons: list[str] = []
    if not report["within_budget"]:
        reasons.append(
            "%d accent pixel(s) exceed the declared budget of %d" % (len(points), budget)
        )
    if not report["clusters_ok"]:
        reasons.append(
            "%d accent pixel(s) sit in %d cluster(s) smaller than the declared minimum of %d"
            % (sum(undersized), len(undersized), minimum_cluster)
        )
    if not report["edge_ok"]:
        reasons.append(
            "accent-to-base step p90 %.0f exceeds the declared maximum of %.0f"
            % (report["edge_delta_p90"] or 0.0, edge_max or 0.0)
        )
    if accent_base_gap_max is not None and not gap_ok:
        message = (
            "accent is not embedded: accents sit %.1f luma from the material they are set "
            "into (limit %.1f) and the accent boundary steps by %.1f (limit %.1f). Vanilla's "
            "own iron_ore measures 12.2 / 23.3 -- its flecks nearly dissolve in their stone. "
            "Add an embed/rim transition between the accent and the base"
            % (gap, accent_base_gap_max, edge_mean, accent_base_edge_mean_max or 0.0)
        )
        if gap_waived:
            # The verdict passed, so the message must not read as a failure.
            # Reported, not refused -- the same shape as a waived limit.
            waived_reasons.append(message)
        else:
            reasons.append(message)
    reasons.extend(structure_reasons)
    report["reasons"] = reasons
    report["waived_reasons"] = waived_reasons
    return report


# ---------------------------------------------------------------------------
# family axes
# ---------------------------------------------------------------------------


def sprite_axes(
    image: "Image.Image | str | Path",
    *,
    accent_colors: Sequence[object] = (),
    base_colors: Sequence[object] = (),
    palette: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The hue and value axes one sprite occupies, accents excluded.

    Accents are removed from the measurement on purpose. A family is not
    inconsistent because one member carries a warm highlight line; it is
    inconsistent when the *material* the two share has drifted to a different
    hue or a different value. Reporting the accent share separately keeps both
    facts visible at once.
    """
    loaded = _load(image)
    pixels = _opaque_map(loaded)
    accent, mode = accent_points(
        loaded, accent_colors=accent_colors, base_colors=base_colors, palette=palette
    )
    base = {point: colour for point, colour in pixels.items() if point not in accent}
    if not base:
        return {
            "sprite": str(image) if not isinstance(image, Image.Image) else None,
            "base_pixels": 0,
            "accent_pixels": len(accent),
            "accent_share": round(len(accent) / float(max(len(pixels), 1)), 4),
            "detection": mode,
            "hue_turns": None,
            "hue_degrees": None,
            "accent_hue_turns": None,
            "accent_hue_degrees": None,
            "chroma_mean": 0.0,
            "luma_mean": 0.0,
            "luma_std": 0.0,
        }
    turns: list[float] = []
    weights: list[float] = []
    lumas: list[float] = []
    chromas: list[float] = []
    for colour in base.values():
        hue, _saturation, _value = hsv(colour)
        lumas.append(luma(colour))
        chromas.append(chroma(colour))
        if chroma(colour) >= 12.0:
            turns.append(hue)
            weights.append(chroma(colour))
    accent_turns: list[float] = []
    accent_weights: list[float] = []
    for colour in (pixels[point] for point in accent):
        _hue, _saturation, _value = hsv(colour)
        if chroma(colour) >= 12.0:
            accent_turns.append(_hue)
            accent_weights.append(chroma(colour))
    mean_hue = _circular_mean(turns, weights)
    accent_hue = _circular_mean(accent_turns, accent_weights)
    mean_luma = sum(lumas) / len(lumas)
    variance = sum((value - mean_luma) ** 2 for value in lumas) / len(lumas)
    return {
        "sprite": str(image) if not isinstance(image, Image.Image) else None,
        "base_pixels": len(base),
        "accent_pixels": len(accent),
        "accent_share": round(len(accent) / float(max(len(pixels), 1)), 4),
        "detection": mode,
        "hue_turns": None if mean_hue is None else round(mean_hue, 5),
        "hue_degrees": None if mean_hue is None else round(mean_hue * 360.0, 1),
        # The accent's own hue, measured the same way as the material's. A
        # family may carry an accent; what it may not do is carry a *different*
        # accent on each member, which is the actual "cannot tell they were one
        # thing" failure.
        "accent_hue_turns": None if accent_hue is None else round(accent_hue, 5),
        "accent_hue_degrees": None if accent_hue is None else round(accent_hue * 360.0, 1),
        # ...and its saturation, because two accents can share a hue and still be
        # two palettes: measured on a real family, 28-35 deg identical while
        # saturation ran 0.33 on the ore against 0.68 on the ingot.
        "accent_saturation_mean": (
            round(sum(hsv(pixels[point])[1] for point in accent) / len(accent), 4)
            if accent else None
        ),
        "chroma_mean": round(sum(chromas) / len(chromas), 2),
        "luma_mean": round(mean_luma, 2),
        "luma_std": round(variance ** 0.5, 2),
    }


def family_axes(
    sprites: Sequence["str | Path | Image.Image"],
    *,
    accent_colors: Sequence[object] = (),
    base_colors: Sequence[object] = (),
    palette: dict[str, str] | None = None,
    maximum_hue_span_deg: float = 26.0,
    maximum_luma_span: float = 56.0,
    maximum_chroma_span: float = 46.0,
    maximum_accent_hue_span_deg: float = 14.0,
    maximum_accent_saturation_span: float = 0.30,
    minimum_accent_pixels: int = 4,
) -> dict[str, Any]:
    """Measure whether a set of sprites shares one material axis and one accent.

    Three axes decide the verdict, and they fail differently:

    * the **material hue** arc -- two members that drifted to different hues are
      visibly different materials;
    * the **value** spread -- a member that is a different lightness stops
      reading as the same rock;
    * the **accent hue** arc over the members that carry an accent at all -- one
      member with an orange seam and another with a cyan seam is exactly the
      "can you even tell these were one object" failure, and a base-only axis
      test cannot see it because it deliberately excludes accents.

    A palette-histogram overlap (``family_consistency``) can pass all three
    failures, because two unrelated materials often share a great many quantised
    colours. This is the axis test instead of the histogram test.
    """
    rows = [
        sprite_axes(sprite, accent_colors=accent_colors, base_colors=base_colors, palette=palette)
        for sprite in sprites
    ]
    hues = [row["hue_turns"] for row in rows if row["hue_turns"] is not None]
    lumas = [row["luma_mean"] for row in rows]
    chromas = [row["chroma_mean"] for row in rows]
    accent_hues = [
        row["accent_hue_turns"]
        for row in rows
        if row["accent_pixels"] >= minimum_accent_pixels and row["accent_hue_turns"] is not None
    ]
    hue_span_deg = round(circular_span(hues) * 360.0, 1) if len(hues) >= 2 else None
    luma_span = round(max(lumas) - min(lumas), 2) if len(lumas) >= 2 else None
    chroma_span = round(max(chromas) - min(chromas), 2) if len(chromas) >= 2 else None
    accent_hue_span_deg = (
        round(circular_span(accent_hues) * 360.0, 1) if len(accent_hues) >= 2 else None
    )
    # The second accent axis, and the one that stayed green while the family was
    # visibly two palettes: hue was aligned (28-35 deg) while saturation was not
    # (0.33 on the ore against 0.68 on the ingot). Hue alone cannot see "one is
    # muted earth and the other is bright amber".
    accent_sats = [
        (row["sprite"] or "?", row["accent_saturation_mean"])
        for row in rows
        if row["accent_pixels"] >= minimum_accent_pixels
        and row.get("accent_saturation_mean") is not None
    ]
    accent_sat_span = (
        round(max(value for _name, value in accent_sats) - min(value for _name, value in accent_sats), 4)
        if len(accent_sats) >= 2 else None
    )
    reasons: list[str] = []
    if accent_sat_span is not None and accent_sat_span > maximum_accent_saturation_span:
        listed = ", ".join(
            "%s %.2f" % (Path(str(name)).parent.name or name, value)
            for name, value in sorted(accent_sats, key=lambda item: item[1])
        )
        reasons.append(
            "accent saturation span %.3f exceeds %.3f: the members are not one accent palette. "
            "Per member: %s" % (accent_sat_span, maximum_accent_saturation_span, listed)
        )
    if hue_span_deg is not None and hue_span_deg > maximum_hue_span_deg:
        reasons.append(
            "hue span %.1f deg exceeds %.1f deg: the members are not one material"
            % (hue_span_deg, maximum_hue_span_deg)
        )
    if luma_span is not None and luma_span > maximum_luma_span:
        reasons.append(
            "value span %.1f exceeds %.1f: the members do not share a value axis"
            % (luma_span, maximum_luma_span)
        )
    if chroma_span is not None and chroma_span > maximum_chroma_span:
        reasons.append(
            "chroma span %.1f exceeds %.1f: one member has drifted to a different saturation"
            % (chroma_span, maximum_chroma_span)
        )
    if accent_hue_span_deg is not None and accent_hue_span_deg > maximum_accent_hue_span_deg:
        reasons.append(
            "accent hue span %.1f deg exceeds %.1f deg: the members do not share one accent colour"
            % (accent_hue_span_deg, maximum_accent_hue_span_deg)
        )
    return {
        "members": rows,
        "count": len(rows),
        "hue_span_degrees": hue_span_deg,
        "luma_span": luma_span,
        "chroma_span": chroma_span,
        "accent_hue_span_degrees": accent_hue_span_deg,
        "accent_saturation_span": accent_sat_span,
        "accent_saturations": [
            {"sprite": name, "saturation": value} for name, value in accent_sats
        ],
        "maximum_accent_saturation_span": maximum_accent_saturation_span,
        "accent_members": len(accent_hues),
        "limits": {
            "maximum_hue_span_deg": maximum_hue_span_deg,
            "maximum_luma_span": maximum_luma_span,
            "maximum_chroma_span": maximum_chroma_span,
            "maximum_accent_hue_span_deg": maximum_accent_hue_span_deg,
            "minimum_accent_pixels": minimum_accent_pixels,
        },
        "reasons": reasons,
        "consistent": not reasons,
    }


def family_axes_summary(report: dict[str, Any]) -> str:
    """One printable line, so a family verdict fits the CLI without a JSON dump."""
    if report.get("count", 0) < 2:
        return "family: needs at least two sprites to compare axes"
    return (
        "family: hue_span=%s deg  value_span=%s  chroma_span=%s  accent_hue_span=%s deg "
        "(%s member(s) carry an accent)  -> %s"
        % (
            report["hue_span_degrees"],
            report["luma_span"],
            report["chroma_span"],
            report.get("accent_hue_span_degrees"),
            report.get("accent_members", 0),
            "consistent" if report["consistent"] else "INCONSISTENT: " + "; ".join(report["reasons"]),
        )
    )
