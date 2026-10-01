"""Deterministic art-quality evidence: value bands, accent budget, family axes.

The three failures this module exists to make measurable are the ones a human
keeps having to point at by hand:

* a stone or an ingot rendered as equal-value scatter instead of a value ramp;
* "two jarring pixels" -- an accent that lands as an isolated speck rather than
  a readable deposit, and an accent whose edge against its base is a hard step;
* a family whose members share a palette histogram but not a hue or value axis,
  so nobody can tell they are the same object.

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
) -> dict[str, Any]:
    """Count the accent pixels a sprite spent, and check every declared budget.

    Three independent questions, because they fail differently:

    * ``budget`` -- how many accent pixels did the sprite use? A plain block
      declares 0 and must come out at 0.
    * ``minimum_cluster`` -- is any accent a lone speck rather than a deposit?
    * ``edge_max`` -- how hard is the step from an accent pixel to the base
      material next to it? This is the number behind "橙色和蓝色的边缘要拖突兀有多突兀".
    """
    loaded = _load(image)
    pixels = _opaque_map(loaded)
    points, mode = accent_points(
        loaded,
        accent_colors=accent_colors,
        base_colors=base_colors,
        palette=palette,
        accent_tolerance=accent_tolerance,
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
    report["accent_luma_mean"] = (
        round(sum(accent_lumas) / len(accent_lumas), 2) if accent_lumas else None
    )
    report["within_budget"] = budget is None or len(points) <= budget
    report["clusters_ok"] = sum(undersized) == 0
    report["edge_ok"] = edge_max is None or (report["edge_delta_p90"] or 0.0) <= edge_max
    report["budget"] = budget
    report["consistent"] = bool(
        report["within_budget"] and report["clusters_ok"] and report["edge_ok"]
    )
    reasons: list[str] = []
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
    report["reasons"] = reasons
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
    reasons: list[str] = []
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
