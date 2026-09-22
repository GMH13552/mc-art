"""Family contracts: one object, many states, one visual language."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .appearance import _resolve_colors
from .contracts import ReferenceAsset
from .geometry import compile_geometry


def _reference_group(reference: ReferenceAsset) -> str | None:
    """The logical asset a reference came from, when the live source recorded it."""
    for note in reference.notes:
        if note.startswith("group="):
            value = note.split("=", 1)[1].strip()
            return value or None
    return None

def _anchor_paint(member_dir: Path, selected_round: Any) -> tuple[Any, Any] | None:
    """The appearance and geometry one finished member actually rendered with.

    Read back from the member's own report so the family inherits a plan the
    model produced, not a plan the family invented.
    """
    try:
        index = int(selected_round)
    except (TypeError, ValueError):
        index = 0
    generated = member_dir / ("round_%02d" % index) / "generated"
    try:
        appearance = appearance_from_dict(
            json.loads((generated / "appearance.json").read_text(encoding="utf-8"))
        )
        geometry = geometry_from_dict(
            json.loads((generated / "geometry.json").read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return appearance, geometry

def _member_references(member_dir: Path, selected_round: Any) -> list[dict[str, Any]]:
    """The reference list one finished member recorded for its selected round."""
    try:
        index = int(selected_round)
    except (TypeError, ValueError):
        index = 0
    references_path = member_dir / ("round_%02d" % index) / "generated" / "references.json"
    try:
        entries = json.loads(references_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return entries if isinstance(entries, list) else []

def _member_shape_host(member_dir: Path, member_name: str, selected_round: Any) -> Path | None:
    """The reference one member was actually conformed to.

    Read back from the member's own report so the continuity baseline is the
    source family the run really used, not a second guess at it. The match is
    the same suffix rule the geometry stage uses, or the baseline would
    compare a frame against a sibling frame it was never conformed to.
    """
    entries = _member_references(member_dir, selected_round)
    candidates: list[tuple[str, Path]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        roles = entry.get("roles") or []
        if "shape" not in roles:
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not Path(path).exists():
            continue
        name = str(entry.get("name") or "")
        member = (name.rsplit(":", 1)[-1] if ":" in name else name).strip().lower()
        candidates.append((member, Path(path)))
    if not candidates:
        return None
    wanted = str(member_name or "").strip().lower()
    best: tuple[int, Path] | None = None
    for member, path in candidates:
        if not member or not wanted:
            continue
        if member == wanted:
            score = len(member) + 1
        elif wanted.endswith(member) or member.endswith(wanted):
            score = len(member)
        else:
            continue
        if best is None or score > best[0]:
            best = (score, path)
    return best[1] if best is not None else candidates[0][1]

def _member_shape_group(references: list[dict[str, Any]]) -> str | None:
    """The logical asset a finished member's shape reference came from.

    Read back from the member's own report rather than assumed, so a family
    propagates a reading only between frames of the same source object.
    """
    group: str | None = None
    for entry in references:
        if not isinstance(entry, dict):
            continue
        roles = entry.get("roles") or []
        if "shape" not in roles:
            continue
        for note in entry.get("notes") or []:
            if isinstance(note, str) and note.startswith("group="):
                group = note.split("=", 1)[1].strip() or None
                break
        if group:
            break
    return group

def remap_palette_to_anchor(
    palette: dict[str, str],
    ramp: list[tuple[int, int, int]],
) -> dict[str, str]:
    """Move an authored palette onto a family anchor's ramp, keeping its order.

    The model names its swatches and decides their dark/mid/light order; the
    family owns the actual values. Entries are re-assigned by luma rank, so the
    authored structure survives while every member of the set resolves to the
    same colours. This is a family-level contract the caller asked for, not a
    per-asset colour post-process.
    """
    if not palette or not ramp:
        return dict(palette)

    def luma(value: str) -> float:
        text = str(value).strip().lstrip("#")
        if len(text) != 6:
            return 128.0
        try:
            red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
        except ValueError:
            return 128.0
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    ordered = sorted(palette.items(), key=lambda item: luma(item[1]))
    ranked = sorted(ramp, key=lambda colour: 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2])
    result: dict[str, str] = {}
    for index, (name, _value) in enumerate(ordered):
        position = round(index * (len(ranked) - 1) / float(max(len(ordered) - 1, 1)))
        red, green, blue = ranked[position]
        result[name] = "#%02X%02X%02X" % (red, green, blue)
    return result

def remap_appearance_to_anchor(
    appearance: Any,
    ramp: list[tuple[int, int, int]],
) -> Any:
    """Force every colour a member named onto the family's ramp.

    ``remap_palette_to_anchor`` only rewrites *named* swatches, and a live bow
    family showed why that is not enough: three of the four frames wrote
    literal hex colours straight into their parts, so the anchor's ramp never
    reached a single pixel and the four frames of one bow shipped in four
    unrelated palettes. Named swatches keep their names; every literal the
    member used is ranked by luma against the ramp, so the member's own light
    to dark structure survives while the values become the family's.
    """
    if not ramp:
        return appearance
    palette = remap_palette_to_anchor(appearance.palette, ramp)
    ranked = sorted(
        ramp, key=lambda colour: 0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]
    )

    def luma(value: str) -> float:
        text = str(value).strip().lstrip("#")
        try:
            red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
        except (ValueError, IndexError):
            return 128.0
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    literals: set[str] = set()
    for part in appearance.parts.values():
        literals.update(colour for colour in part.colors if _HEX_COLOUR.match(str(colour)))
    if appearance.outline_color and _HEX_COLOUR.match(str(appearance.outline_color)):
        literals.add(appearance.outline_color)
    pixel_map = appearance.pixel_map
    if isinstance(pixel_map, dict) and isinstance(pixel_map.get("legend"), dict):
        literals.update(
            value for value in pixel_map["legend"].values() if _HEX_COLOUR.match(str(value))
        )
    ordered = sorted(literals, key=luma)
    literal_map: dict[str, str] = {}
    for index, colour in enumerate(ordered):
        position = round(index * (len(ranked) - 1) / float(max(len(ordered) - 1, 1)))
        red, green, blue = ranked[position]
        literal_map[colour] = "#%02X%02X%02X" % (red, green, blue)

    def resolved(colour: str) -> str:
        return literal_map.get(colour, colour)

    parts = {
        part_id: replace(part, colors=[resolved(colour) for colour in part.colors])
        for part_id, part in appearance.parts.items()
    }
    updated_map = pixel_map
    if isinstance(pixel_map, dict) and isinstance(pixel_map.get("legend"), dict):
        updated_map = dict(pixel_map)
        updated_map["legend"] = {
            key: resolved(str(value)) for key, value in pixel_map["legend"].items()
        }
    return replace(
        appearance,
        palette=palette,
        parts=parts,
        outline_color=(resolved(appearance.outline_color) if appearance.outline_color else None),
        pixel_map=updated_map,
    )

def inherit_anchor_paint(
    appearance: Any,
    geometry: Any,
    anchor_appearance: Any,
    anchor_geometry: Any,
    minimum_overlap: float = 0.5,
) -> tuple[Any, dict[str, Any]]:
    """Give each member part the anchor part's whole paint specification.

    The family contract already forces the colour *values* onto one ramp, and a
    live crystal bow showed why that is not enough: each frame still authored
    its own shading vocabulary, so the body resolved into six value bands on
    one frame and four dark ones on the next. The four frames of one bow read
    as four materials.

    Matching is by mask overlap, never by part name: a member calls the same
    region bow_body, crystal_bow_body or crystal_bow_body_upper, and only the
    pixels agree. Overlap is also the guard -- members of a plain set
    (helmet, chestplate) sit on different atlases, so nothing matches and each
    keeps its own paint.

    Returns the revised appearance and a per-part audit of what was matched.
    """
    try:
        member = compile_geometry(geometry)
        anchor = compile_geometry(anchor_geometry)
    except (KeyError, TypeError, ValueError):
        return appearance, {}
    anchor_ramps: dict[str, list[str]] = {}
    for part_id, spec in anchor_appearance.parts.items():
        anchor_ramps[part_id] = [
            "#%02X%02X%02X" % colour
            for colour in _resolve_colors(spec, anchor_appearance.palette)
        ]

    matched: dict[str, Any] = {}
    parts: dict[str, Any] = {}
    for part_id, spec in appearance.parts.items():
        parts[part_id] = spec
        mask = member.part_masks.get(part_id)
        if mask is None:
            continue
        own = [
            (x, y)
            for y in range(member.height)
            for x in range(member.width)
            if mask.getpixel((x, y)) > 0
        ]
        if not own:
            continue
        best_id, best_score = None, 0.0
        for candidate_id, candidate_spec in anchor_appearance.parts.items():
            candidate = anchor.part_masks.get(candidate_id)
            if candidate is None:
                continue
            shared = sum(1 for x, y in own if candidate.getpixel((x, y)) > 0)
            if not shared:
                continue
            # Intersection over union, not coverage of this part alone. A
            # nocked arrow sits *inside* the bow body's mask, so coverage
            # alone says "same region" and hands the arrow the body's dark
            # crystal ramp -- which is exactly the arrow the prompt asked to
            # declare. Two parts are the same region only if they mostly
            # contain each other.
            candidate_points = sum(
                1
                for y in range(anchor.height)
                for x in range(anchor.width)
                if candidate.getpixel((x, y)) > 0
            )
            union = len(own) + candidate_points - shared
            if union <= 0:
                continue
            score = shared / float(union)
            if score > best_score:
                best_id, best_score = candidate_id, score
        if best_id is None or best_score < minimum_overlap:
            continue
        source = anchor_appearance.parts[best_id]
        parts[part_id] = replace(
            spec,
            colors=list(anchor_ramps[best_id]),
            shade_axis=source.shade_axis,
            noise=source.noise,
            highlight_ratio=source.highlight_ratio,
            marks=list(source.marks),
        )
        matched[part_id] = {"anchor_part": best_id, "overlap": round(best_score, 3)}

    if not matched:
        return appearance, {}
    # The anchor's own hand-authored composition is the pattern the request
    # asked to keep, so it carries across the frames that locked onto the same
    # object. A member that matched almost nothing keeps its own composition.
    # A declared part owns geometry here only when its mask actually holds
    # pixels; compile_geometry creates an empty mask for every declared part,
    # so presence alone would count paint-only parts as physical.
    physical = [
        part_id
        for part_id in appearance.parts
        if part_id in member.part_masks
        and any(
            member.part_masks[part_id].getpixel((x, y)) > 0
            for y in range(member.height)
            for x in range(member.width)
        )
    ]
    carries_composition = len(matched) >= max(1, len(physical) // 2)
    updated = replace(
        appearance,
        parts=parts,
        pixel_map=anchor_appearance.pixel_map if carries_composition else appearance.pixel_map,
        outline_color=(anchor_appearance.outline_color if carries_composition else appearance.outline_color),
        outline_width=(anchor_appearance.outline_width if carries_composition else appearance.outline_width),
    )
    return updated, {
        "parts": matched,
        "composition_from_anchor": carries_composition,
    }


# Re-exported so a family contract and the numbers that check it travel together.
from .metrics import (  # noqa: E402
    family_consistency,
    frame_continuity,
    sprite_palette_profile,
    sprite_palette_ramp,
)
