"""Deterministic geometry and render validation.

Validation does not need to know a closed vocabulary such as sword or dagger.
It checks the measurable requirements supplied by a runtime geometry plan:
parts, adjacency, margins, topology and planner-defined numeric constraints.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import atan2, degrees, sqrt
import re
from typing import Iterable, Protocol

from PIL import Image

from .contracts import AssetForm, GeometrySpec, ShapeDescriptor, ValidationResult
from .geometry import CompiledGeometry


def _points(mask: Image.Image, threshold: int = 1) -> set[tuple[int, int]]:
    image = mask.convert("L")
    return {
        (x, y)
        for y in range(image.height)
        for x in range(image.width)
        if image.getpixel((x, y)) >= threshold
    }


def _components(points: set[tuple[int, int]]) -> int:
    """Count visually connected sprite regions, including diagonal pixel steps.

    A one-pixel Minecraft diagonal is intentionally 8-connected. Treating it
    as a collection of separate objects would reject valid blades, branches and
    wings merely because their staircase edges have no orthogonal neighbour.
    """
    unseen = set(points)
    count = 0
    while unseen:
        count += 1
        queue: deque[tuple[int, int]] = deque([unseen.pop()])
        while queue:
            x, y = queue.popleft()
            for neighbour in (
                (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1),
                (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1), (x + 1, y + 1),
            ):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    queue.append(neighbour)
    return count


def _bbox(points: set[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def _max_row_run(points: set[tuple[int, int]]) -> int:
    longest = 0
    by_row: dict[int, list[int]] = {}
    for x, y in points:
        by_row.setdefault(y, []).append(x)
    for xs in by_row.values():
        ordered = sorted(xs)
        run = 0
        previous = None
        for x in ordered:
            run = run + 1 if previous is not None and x == previous + 1 else 1
            longest = max(longest, run)
            previous = x
    return longest


def _principal_axis(points: set[tuple[int, int]]) -> tuple[float, float]:
    if len(points) < 2:
        return 0.0, 0.0
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    xx = yy = xy = 0.0
    for x, y in points:
        dx, dy = x - mean_x, y - mean_y
        xx += dx * dx
        yy += dy * dy
        xy += dx * dy
    xx /= len(points)
    yy /= len(points)
    xy /= len(points)
    angle = 0.5 * atan2(2.0 * xy, xx - yy)
    trace = xx + yy
    determinant = max(xx * yy - xy * xy, 0.0)
    root = sqrt(max(trace * trace - 4.0 * determinant, 0.0))
    major, minor = (trace + root) / 2.0, (trace - root) / 2.0
    return degrees(angle), (major - minor) / max(major + minor, 1e-9)


def _touches(a: set[tuple[int, int]], b: set[tuple[int, int]]) -> bool:
    if a & b:
        return True
    return any(
        (x + dx, y + dy) in b
        for x, y in a
        for dx, dy in (
            (1, 0), (-1, 0), (0, 1), (0, -1),
            (1, 1), (1, -1), (-1, 1), (-1, -1),
        )
    )


def geometry_metrics(compiled: CompiledGeometry) -> dict[str, float | int]:
    all_points = _points(compiled.mask)
    bbox = _bbox(all_points)
    metrics: dict[str, float | int] = {
        "opaque_pixels": len(all_points),
        "occupancy_ratio": len(all_points) / float(compiled.width * compiled.height),
        "components": _components(all_points),
    }
    orientation_degrees, axis_anisotropy = _principal_axis(all_points)
    metrics["orientation_degrees"] = orientation_degrees
    metrics["axis_anisotropy"] = axis_anisotropy
    if bbox is None:
        metrics.update(
            {
                "bbox_left": 0,
                "bbox_top": 0,
                "bbox_width": 0,
                "bbox_height": 0,
                "aspect_ratio": 0.0,
                "margin_left": 0,
                "margin_top": 0,
                "margin_right": 0,
                "margin_bottom": 0,
            }
        )
    else:
        left, top, right, bottom = bbox
        box_width, box_height = right - left, bottom - top
        metrics.update(
            {
                "bbox_left": left,
                "bbox_top": top,
                "bbox_width": box_width,
                "bbox_height": box_height,
                "bbox_width_ratio": box_width / float(compiled.width),
                "bbox_height_ratio": box_height / float(compiled.height),
                "aspect_ratio": box_width / float(max(box_height, 1)),
                "margin_left": left,
                "margin_top": top,
                "margin_right": compiled.width - right,
                "margin_bottom": compiled.height - bottom,
            }
        )
    for part_id, mask in compiled.part_masks.items():
        part_points = _points(mask)
        metrics["%s_pixels" % part_id] = len(part_points)
        metrics["%s_ratio" % part_id] = len(part_points) / float(max(len(all_points), 1))
        metrics["%s_components" % part_id] = _components(part_points)
        metrics["%s_max_row_run" % part_id] = _max_row_run(part_points)
        part_bbox = _bbox(part_points)
        if part_bbox is None:
            metrics["%s_bbox_width" % part_id] = 0
            metrics["%s_bbox_height" % part_id] = 0
            metrics["%s_bbox_width_ratio" % part_id] = 0.0
            metrics["%s_bbox_height_ratio" % part_id] = 0.0
            metrics["%s_aspect_ratio" % part_id] = 0.0
        else:
            left, top, right, bottom = part_bbox
            part_width, part_height = right - left, bottom - top
            metrics["%s_bbox_width" % part_id] = part_width
            metrics["%s_bbox_height" % part_id] = part_height
            metrics["%s_bbox_width_ratio" % part_id] = part_width / float(compiled.width)
            metrics["%s_bbox_height_ratio" % part_id] = part_height / float(compiled.height)
            metrics["%s_aspect_ratio" % part_id] = part_width / float(max(part_height, 1))
    return metrics


def validate_geometry(spec: GeometrySpec, compiled: CompiledGeometry, form: AssetForm,
                      minimum_margin: int | None = None) -> ValidationResult:
    metrics = geometry_metrics(compiled)
    errors: list[str] = []
    warnings: list[str] = []
    all_points = _points(compiled.mask)
    if not all_points:
        errors.append("geometry mask is empty")
    if metrics["components"] > 4:
        errors.append("geometry is fragmented into %s connected components" % metrics["components"])

    for part in spec.parts:
        points = _points(compiled.part_masks[part.id])
        if part.required and not part.paint_only and not points:
            errors.append("required part %s is empty" % part.id)
        elif points and _components(points) > 3:
            warnings.append("part %s has %s disconnected components" % (part.id, _components(points)))

    # Distinct labelled sections should not collapse into one almost-identical
    # mask. A small overlap is useful for a joint; near-total overlap means the
    # planner has lost the part boundary and usually produces a generic blob.
    required_points = {
        part.id: _points(compiled.part_masks[part.id])
        for part in spec.parts
        if part.required and not part.paint_only
    }
    part_by_id = {part.id: part for part in spec.parts}
    required_ids = list(required_points)
    for index, first_id in enumerate(required_ids):
        for second_id in required_ids[index + 1:]:
            first = required_points[first_id]
            second = required_points[second_id]
            overlap = len(first & second) / float(max(min(len(first), len(second)), 1))
            key = "overlap_%s_%s_ratio" % (first_id, second_id)
            metrics[key] = overlap
            detail_roles = {
                "highlight", "accent", "detail", "mark", "shadow", "edge", "texture",
                "motif", "inlaid", "embedded", "inlay", "overlay", "emblem",
                "eye", "gem", "socket", "boss",
            }
            first_role = part_by_id[first_id].style_role.lower()
            second_role = part_by_id[second_id].style_role.lower()
            # Match role words instead of substrings.  A host described as
            # "host support for an inlaid motif" is still a physical support;
            # treating the word ``inlaid`` as a detail role hid the very
            # overlap that made the eye consume the guard in earlier runs.
            first_words = set(re.findall(r"[a-z]+", first_role))
            second_words = set(re.findall(r"[a-z]+", second_role))
            first_text = (part_by_id[first_id].meaning + " " + first_role).lower()
            second_text = (part_by_id[second_id].meaning + " " + second_role).lower()
            # A local inlay is deliberately drawn over its host support, so
            # its alpha mask can overlap the host completely.  Only treat a
            # part as an overlay when its own role identifies it as one; a
            # host phrase such as "host support for the inlaid eye" must stay
            # a physical support and must not disable this check for unrelated
            # support pairs.
            first_is_host = "host support" in first_role or first_role.strip() in {"support", "primary support"}
            second_is_host = "host support" in second_role or second_role.strip() in {"support", "primary support"}
            first_is_overlay = bool((first_words & detail_roles) and not first_is_host)
            second_is_overlay = bool((second_words & detail_roles) and not second_is_host)
            if not first_is_overlay and any(token in first_text for token in ("local motif", "embedded", "inlaid", "inlay", "example", "gem", "emblem")) and not first_is_host:
                first_is_overlay = True
            if not second_is_overlay and any(token in second_text for token in ("local motif", "embedded", "inlaid", "inlay", "example", "gem", "emblem")) and not second_is_host:
                second_is_overlay = True
            is_detail = first_is_overlay or second_is_overlay
            if form in {AssetForm.ITEM, AssetForm.CROSS} and not is_detail and min(len(first), len(second)) >= 10 and overlap > 0.80:
                errors.append(
                    "required parts %s and %s overlap %.0f%%; keep labelled sections visibly distinct"
                    % (first_id, second_id, overlap * 100.0)
                )

    for connection in spec.connections:
        a = _points(compiled.part_masks[connection.a])
        b = _points(compiled.part_masks[connection.b])
        part_by_id = {part.id: part for part in spec.parts}
        if connection.required and not part_by_id[connection.a].paint_only and not part_by_id[connection.b].paint_only and not _touches(a, b):
            errors.append("required parts %s and %s do not touch" % (connection.a, connection.b))

    if form == AssetForm.ENTITY_UV:
        if not spec.uv_regions:
            errors.append("entity_uv geometry requires explicit uv_regions; do not guess a model layout")
        regions_by_part: dict[str, list[set[tuple[int, int]]]] = {}
        for region in spec.uv_regions:
            left, top, right, bottom = region.bbox
            region_points = {(x, y) for y in range(top, bottom) for x in range(left, right)}
            regions_by_part.setdefault(region.part_id, []).append(region_points)
            painted = _points(compiled.part_masks[region.part_id]) & region_points
            metrics["uv_%s_pixels" % region.id] = len(painted)
            if region.required and not painted:
                errors.append("required UV region %s has no pixels for part %s" % (region.id, region.part_id))
        for part in spec.parts:
            part_points = _points(compiled.part_masks[part.id])
            allowed = set().union(*regions_by_part.get(part.id, [])) if part.id in regions_by_part else set()
            outside = part_points - allowed
            if outside:
                errors.append(
                    "part %s paints %d pixel(s) outside its declared UV regions" % (part.id, len(outside))
                )
            if part.required and not part.paint_only and part_points and not allowed:
                errors.append("required entity part %s has no declared UV region" % part.id)

    if form == AssetForm.BLOCK_MULTI:
        if not spec.uv_regions:
            errors.append("block_multi geometry requires top/front/side UV regions")
        faces = {region.face.lower() for region in spec.uv_regions}
        if "top" not in faces:
            errors.append("block_multi layout is missing a top face")
        if not ({"front", "north", "south"} & faces):
            errors.append("block_multi layout is missing a front face")
        if not ({"right", "east", "west", "side", "left"} & faces):
            errors.append("block_multi layout is missing a side face")
        regions_by_part: dict[str, list[set[tuple[int, int]]]] = {}
        for region in spec.uv_regions:
            left, top, right, bottom = region.bbox
            region_points = {(x, y) for y in range(top, bottom) for x in range(left, right)}
            regions_by_part.setdefault(region.part_id, []).append(region_points)
            painted = _points(compiled.part_masks[region.part_id]) & region_points
            metrics["uv_%s_pixels" % region.id] = len(painted)
            if region.required and not painted:
                errors.append("required block face %s has no pixels for part %s" % (region.id, region.part_id))
        for part in spec.parts:
            part_points = _points(compiled.part_masks[part.id])
            allowed = set().union(*regions_by_part.get(part.id, [])) if part.id in regions_by_part else set()
            if part_points - allowed:
                errors.append("block part %s paints outside its declared face regions" % part.id)

    if minimum_margin is None:
        # Edge occupancy is part of the model-authored silhouette.  Inventory
        # sprites and crosses may legitimately touch a canvas edge (the
        # vanilla sword reference does), so a one-pixel inset is not a generic
        # validity rule.  Callers that truly need padding can still pass an
        # explicit ``minimum_margin`` or emit a model-authored constraint.
        minimum_margin = 0
    if all_points and minimum_margin > 0:
        for side in ("left", "top", "right", "bottom"):
            if int(metrics["margin_%s" % side]) < minimum_margin:
                errors.append("geometry violates %dpx %s transparent margin" % (minimum_margin, side))

    for constraint in spec.constraints:
        value = metrics.get(constraint.metric)
        if value is None:
            errors.append("constraint references unavailable metric: %s" % constraint.metric)
            continue
        numeric = float(value)
        # Principal-axis orientation has a sign because the image y-axis
        # points downward. Constraints express an undirected diagonal angle,
        # so compare its magnitude; otherwise a valid -50° blade fails a
        # 30–60° diagonal constraint while its mirrored +50° counterpart
        # passes.
        if (
            constraint.metric == "orientation_degrees"
            and (constraint.minimum is None or constraint.minimum >= 0)
            and (constraint.maximum is None or constraint.maximum >= 0)
        ):
            numeric = abs(numeric)
        if constraint.minimum is not None and numeric < constraint.minimum:
            errors.append(
                "%s: %s=%.3f is below %.3f" % (
                    constraint.message or constraint.metric,
                    constraint.metric,
                    numeric,
                    constraint.minimum,
                )
            )
        if constraint.maximum is not None and numeric > constraint.maximum:
            errors.append(
                "%s: %s=%.3f exceeds %.3f" % (
                    constraint.message or constraint.metric,
                    constraint.metric,
                    numeric,
                    constraint.maximum,
                )
            )
    return ValidationResult(
        passed=not errors,
        stage="geometry",
        metrics=metrics,
        errors=errors,
        warnings=warnings,
    )


def validate_render_alpha(
    compiled: CompiledGeometry,
    rendered: Image.Image,
    expected_mask: Image.Image | None = None,
) -> ValidationResult:
    expected = (expected_mask or compiled.mask).convert("L")
    actual = rendered.convert("RGBA").getchannel("A")
    if actual.size != expected.size:
        return ValidationResult(
            passed=False,
            stage="render_alpha",
            metrics={"expected_width": expected.width, "actual_width": actual.width},
            errors=["rendered image size differs from geometry mask"],
        )
    mismatch = sum(
        (expected.getpixel((x, y)) > 0) != (actual.getpixel((x, y)) > 0)
        for y in range(expected.height)
        for x in range(expected.width)
    )
    return ValidationResult(
        passed=mismatch == 0,
        stage="render_alpha",
        metrics={"alpha_exact": mismatch == 0, "alpha_mismatch_pixels": mismatch},
        errors=[] if mismatch == 0 else ["rendered alpha differs from the locked expected mask"],
    )


def validate_style(
    appearance: object,
    rendered: Image.Image,
    accent_points: set[tuple[int, int]] | None = None,
) -> ValidationResult:
    """Check the art-quality budgets the plan declared, on the finished sprite.

    Three declarations become three hard errors, because each one is a thing a
    person asked for by name and then had to point at by hand:

    * ``accent_budget`` -- "a plain block has no accent pixels". Zero is a
      budget like any other.
    * ``accent_min_cluster`` -- "not one or two jarring dots". Cluster sizes are
      reported; the renderer repaints undersized ones only if ``accent_cleanup``
      asked it to.
    * ``accent_edge_max`` -- "the orange and the blue do not read as one thing":
      the luma step where the accent meets its base material.
    * ``band_maximum_isolated`` / ``band_maximum_step`` -- "a ramp, not
      equal-value scatter".

    With nothing declared the stage still reports the numbers and passes, so a
    caller can watch the metric before it decides to gate on it.
    """
    from .appearance import declared_base_swatches
    from .style import accent_audit, band_report

    base_swatches = declared_base_swatches(appearance)  # type: ignore[arg-type]
    audit = accent_audit(
        rendered,
        accent_colors=getattr(appearance, "accent_colors", []),
        base_colors=base_swatches,
        palette=getattr(appearance, "palette", None),
        budget=getattr(appearance, "accent_budget", None),
        minimum_cluster=getattr(appearance, "accent_min_cluster", 1),
        edge_max=getattr(appearance, "accent_edge_max", None),
        motif_repeat_max=getattr(appearance, "accent_motif_repeat_max", 2),
        layout_min_size_cv=getattr(appearance, "accent_layout_min_size_cv", 0.15),
        layout_min_spacing_cv=getattr(appearance, "accent_layout_min_spacing_cv", 0.15),
        ramp_min_pixels=getattr(appearance, "accent_ramp_min_pixels", 6),
        ramp_min_levels=getattr(appearance, "accent_ramp_min_levels", 3),
        ramp_max_dominant_share=getattr(appearance, "accent_ramp_max_dominant_share", 0.6),
        ramp_min_monotone=getattr(appearance, "accent_ramp_min_monotone", 0.6),
        bar_fill_max=getattr(appearance, "accent_bar_fill_max", 0.75),
        bar_min_aspect=getattr(appearance, "accent_bar_min_aspect", 1.8),
        bar_min_pixels=getattr(appearance, "accent_bar_min_pixels", 10),
        accent_base_gap_min=getattr(appearance, "accent_base_gap_min", 6.0),
        accent_base_gap_max=getattr(appearance, "accent_base_gap_max", 24.0),
        accent_base_edge_mean_max=getattr(appearance, "accent_base_edge_mean_max", 35.0),
        threshold_waiver=getattr(appearance, "threshold_waiver", ""),
        points=accent_points,
    )
    bands = band_report(
        rendered,
        maximum_isolated=getattr(appearance, "band_maximum_isolated", None),
        maximum_step=getattr(appearance, "band_maximum_step", None),
    )
    # The independent scan. Every other gate trusts a declaration; this one does
    # not, because the failure it exists for is a declaration that did not match
    # the picture.
    from .style import unaudited_accent_report

    declared_points = {tuple(point) for point in (accent_points or audit["points"] or [])}
    unaudited, unaudited_reasons = unaudited_accent_report(rendered, declared_points)
    warnings: list[str] = []
    # Two sources deciding the accent is almost always a leftover, and it is
    # exactly the situation where the gates measure one set and the picture shows
    # another.
    if getattr(appearance, "pixel_map", None) and getattr(
        appearance, "accent_from_reference", None
    ):
        warnings.append(
            "this plan declares BOTH appearance.pixel_map and "
            "appearance.accent_from_reference, so two sources decide the accent pixels and the "
            "hand-drawn map paints over the derived one. Remove whichever is the leftover; the "
            "unaudited_accent scan reports what it causes"
        )
    errors = list(audit["reasons"]) + list(bands["reasons"]) + list(unaudited_reasons)
    metrics: dict[str, float | int | str | bool] = {
        "accent_detection": str(audit["detection"]),
        "accent_pixels": int(audit["accent_pixels"]),
        "accent_budget": -1 if audit["budget"] is None else int(audit["budget"]),
        "accent_clusters": int(audit["clusters"]),
        "accent_smallest_cluster": int(audit["smallest_cluster"]),
        "accent_below_minimum_pixels": int(audit["below_minimum_pixels"]),
        "accent_edge_delta_p90": -1.0 if audit["edge_delta_p90"] is None else float(audit["edge_delta_p90"]),
        "accent_within_budget": bool(audit["within_budget"]),
        "accent_motif_repeat": int(audit["structure"]["motif"]["repeat_max"]),
        "accent_motif_distinct": int(audit["structure"]["motif"]["distinct_shapes"]),
        "accent_motif_ok": bool(audit["structure"]["motif"]["ok"]),
        "accent_layout_size_cv": float(audit["structure"]["layout"]["size_cv"]),
        "accent_layout_spacing_cv": float(audit["structure"]["layout"]["spacing_cv"]),
        "accent_layout_quadrants": int(audit["structure"]["layout"]["quadrant_occupancy"]),
        "accent_layout_ok": bool(audit["structure"]["layout"]["ok"]),
        "accent_ramp_levels_min": (
            min(audit["structure"]["ramp"]["levels_used"])
            if audit["structure"]["ramp"]["levels_used"] else -1
        ),
        "accent_ramp_dominant_max": (
            max(audit["structure"]["ramp"]["dominant_shares"])
            if audit["structure"]["ramp"]["dominant_shares"] else -1.0
        ),
        "accent_ramp_monotone_min": (
            min(audit["structure"]["ramp"]["monotone_shares"])
            if audit["structure"]["ramp"]["monotone_shares"] else -1.0
        ),
        "accent_ramp_ok": bool(audit["structure"]["ramp"]["ok"]),
        "accent_bar_clusters": int(audit["structure"]["shape"]["bar_clusters"]),
        "accent_bar_ok": bool(audit["structure"]["shape"]["ok"]),
        "accent_base_gap_mean": audit["accent_base_gap_mean"],
        "accent_base_signed_gap": audit["accent_base_signed_gap"],
        "accent_edge_delta_mean": audit["edge_delta_mean"],
        "accent_base_gap_ok": bool(audit["base_gap_ok"]),
        # Exported so a re-audit of the delivered PNG (the example build does
        # exactly that) judges the same pixel set the renderer placed, instead of
        # re-deriving it by colour distance and losing an embedded accent.
        "accent_points": sorted(audit["points"]) if audit.get("points") else None,
        "accent_luma_mean": audit["accent_luma_mean"],
        "base_luma_mean": audit["base_luma_mean"],
        "accent_structure_ok": bool(audit["structure"]["ok"]),
        "accent_unaudited_pixels": int(unaudited["unaudited_pixels"]),
        "accent_unaudited_clusters": int(unaudited["clusters"]),
        "accent_unaudited_ok": bool(unaudited["ok"]),
        "band_count": int(bands["band_count"]),
        "band_isolated_pixels": int(bands["isolated_pixels"]),
        "band_isolated_share": float(bands["isolated_share"]),
        "band_mean_neighbour_step": float(bands["mean_neighbour_step"]),
        "band_verdict": str(bands["verdict"]),
    }
    warnings: list[str] = []
    if audit["budget"] is None and audit["accent_pixels"]:
        warnings.append(
            "%d accent pixel(s) reported; declare accent_budget to make this a gate"
            % audit["accent_pixels"]
        )
    return ValidationResult(
        passed=not errors,
        stage="style",
        metrics=metrics,
        errors=errors,
        warnings=warnings,
    )


def validate_reference_contract(
    selection: dict[str, object],
    *,
    shape_edit_mode: str,
    waiver: str = "",
    asset: str = "",
) -> ValidationResult:
    """Refuse to invent a contour in silence.

    The engine already knew when a render had no reference at all -- it wrote
    ``step: no-references-offered`` into ``reference_selection.json`` -- and then
    drew the asset anyway, because nothing read that field. A user found the
    result and said "the raw one doesn't reference raw iron at all", which was
    correct and had been true the whole time.

    So the fact is promoted to a warning always, and to an error whenever the
    plan is not a plain recolour: an object with a vanilla counterpart must take
    its shape from that counterpart, and a genuinely new contour must say why in
    ``reference_waiver``.
    """
    chosen = selection.get("chosen") if isinstance(selection, dict) else None
    step = str(selection.get("step", "unknown")) if isinstance(selection, dict) else "unknown"
    counts = selection.get("counts") if isinstance(selection, dict) else None
    offered = int(counts.get("offered", 0)) if isinstance(counts, dict) else 0
    detail = str(selection.get("step_detail", "")) if isinstance(selection, dict) else ""
    mode = str(shape_edit_mode or "").strip().lower()
    reason = str(waiver or "").strip()

    metrics: dict[str, float | int | str | bool] = {
        "step": step,
        "offered": offered,
        "chosen": bool(chosen),
        "shape_edit_mode": mode,
        "waiver_present": bool(reason),
    }
    errors: list[str] = []
    warnings: list[str] = []
    if chosen:
        return ValidationResult(passed=True, stage="reference", metrics=metrics)

    headline = (
        "NO REFERENCE USED (%s): this asset was coloured with no source to learn "
        "from, so nothing about its shape came from anything." % step
    )
    warnings.append(headline if not detail else headline + " " + detail)
    if mode == "appearance_only":
        warnings.append(
            "shape_edit_mode is appearance_only but nothing was offered to conform to; "
            "the contour is therefore the authored mask, not a source's"
        )
        return ValidationResult(
            passed=True, stage="reference", metrics=metrics, warnings=warnings
        )
    if reason:
        warnings.append("accepted because reference_waiver says: %s" % reason)
        return ValidationResult(
            passed=True, stage="reference", metrics=metrics, warnings=warnings
        )
    errors.append(
        "no reference was offered and shape_edit_mode is %r, so this asset invents its "
        "own contour with nothing to learn from. If the object exists in the game, use "
        "its texture as the shape authority (appearance_only); if the contour really is "
        "new, say why in descriptor.reference_waiver%s"
        % (mode or "unset", " (%s)" % asset if asset else "")
    )
    return ValidationResult(
        passed=False, stage="reference", metrics=metrics, errors=errors, warnings=warnings
    )


def _name_tokens(value: str) -> set[str]:
    """Content words of an asset name, with the packaging noise removed."""
    import re

    generic = {"example", "generated", "demo", "block", "item", "texture", "tile"}
    return {
        token for token in re.split(r"[^a-z0-9]+", str(value).lower())
        if token and token not in generic
    }


def validate_reference_class(
    declared_class: str,
    declared_layer: str,
    references: list[object],
    *,
    chosen_name: str,
    available: list[object] | None = None,
    asset: str = "",
) -> ValidationResult:
    """Is the reference that was actually chosen the KIND the plan declared?

    A real project's ``example_mist_stone`` -- a stone declared *shallow* -- attached
    ``deepslate.png``, and its own note said the shallow variant was available and
    "must NOT be chosen". The candidate table could not see it: it scored roles,
    alpha overlap and how many words two filenames share, and "deep" against
    "shallow" is not a word overlap, it is a kind.

    Three separate questions, because they fail differently:

    * does each reference carry the class the ENGINE derives from its own name?
      (a plan can otherwise mislabel what it attached)
    * is the chosen reference the class the plan declared for the asset?
    * is it in the layer the plan declared?

    A plan that declares nothing is not failed -- it is reported as unchecked, so
    a green run never implies a check that did not happen.
    """
    from .refclass import CLASSES, LAYERS, classify, layer_of

    metrics: dict[str, float | int | str | bool] = {
        "declared_class": declared_class or "",
        "declared_layer": declared_layer or "",
        "chosen": chosen_name or "",
        "checked": False,
    }
    problems: list[str] = []
    warnings: list[str] = []

    if declared_class and declared_class not in CLASSES:
        problems.append(
            "descriptor.reference_class '%s' is not a class the engine knows; the published "
            "table is: %s" % (declared_class, ", ".join(CLASSES))
        )
    if declared_layer and declared_layer not in LAYERS:
        problems.append(
            "descriptor.reference_layer '%s' is not a layer the engine knows; the published "
            "table is: %s" % (declared_layer, ", ".join(LAYERS))
        )

    # 1. Does each attached reference match the class it claims to be?
    for reference in references or []:
        name = str(getattr(reference, "name", ""))
        claimed = str(getattr(reference, "declared_class", "") or "")
        if not claimed:
            continue
        derived = classify(name)
        if derived != "unknown" and claimed != derived:
            problems.append(
                "reference '%s' declares class '%s' but the engine derives '%s' from its name "
                "-- a plan may not relabel what it attached" % (name, claimed, derived)
            )

    # 2a. Is the declared class REPRESENTED among the attached references?
    #
    # Not "is the globally-chosen one that class": a base+deposit plan declares the
    # deposit's class and legitimately attaches the base too. `example_starfall_ore` is an
    # ore whose rock is deepslate, so its references are deepslate AND iron_ore, and
    # the ore is what makes it an ore. Requiring the chosen reference to carry the
    # class would refuse a correct plan.
    if declared_class:
        attached_classes = [
            (str(getattr(reference, "name", "")), classify(str(getattr(reference, "name", ""))))
            for reference in references or []
        ]
        metrics["attached_classes"] = ", ".join(
            "%s=%s" % (name, derived) for name, derived in attached_classes
        )
        represented = [name for name, derived in attached_classes if derived == declared_class]
        metrics["class_represented"] = bool(represented)
        if attached_classes and not represented:
            candidates = [
                "%s (%s)" % (
                    str(getattr(item, "name", "")), classify(str(getattr(item, "name", "")))
                )
                for item in (available or [])
                if classify(str(getattr(item, "name", ""))) == declared_class
            ]
            problems.append(
                "%s declares class '%s' but none of its attached reference(s) is one: %s%s"
                % (
                    asset or "this asset",
                    declared_class,
                    metrics["attached_classes"],
                    ". Same-class references offered: %s" % ", ".join(candidates)
                    if candidates
                    else ". No reference of that class was offered at all",
                )
            )

    # 2b. Is the chosen (base) reference in the declared layer?
    chosen = None
    for reference in references or []:
        if str(getattr(reference, "name", "")) == chosen_name:
            chosen = reference
            break
    if chosen is None:
        if declared_class or declared_layer:
            warnings.append(
                "no reference was chosen, so the declared class '%s' was not checked against "
                "anything" % (declared_class or declared_layer)
            )
    else:
        metrics["checked"] = True
        derived_class = classify(str(getattr(chosen, "name", "")))
        derived_layer = layer_of(str(getattr(chosen, "name", "")))
        metrics["chosen_class"] = derived_class
        metrics["chosen_layer"] = derived_layer
        if declared_layer and derived_layer != "unknown" and derived_layer != declared_layer:
            candidates = [
                str(getattr(item, "name", ""))
                for item in (available or [])
                if layer_of(str(getattr(item, "name", ""))) == declared_layer
            ]
            problems.append(
                "%s declares layer '%s' but attached '%s', which lives in the '%s' layer%s"
                % (
                    asset or "this asset",
                    declared_layer,
                    getattr(chosen, "name", ""),
                    derived_layer,
                    ". References in that layer: %s" % ", ".join(candidates)
                    if candidates
                    else "",
                )
            )

    if not (declared_class or declared_layer):
        # Nothing was declared, so nothing was checked -- even though a reference
        # exists to check. `checked` means "a declared kind was compared", not
        # "there was a reference".
        metrics["checked"] = False
        return ValidationResult(
            passed=True,
            stage="reference_class",
            metrics=metrics,
            warnings=[
                "no reference class was declared, so 'the chosen reference is the kind the asset "
                "claims to be' was NOT checked (declare descriptor.reference_class)"
            ],
        )
    if problems:
        return ValidationResult(
            passed=False, stage="reference_class", metrics=metrics, errors=problems,
            warnings=warnings,
        )
    if not metrics["checked"] and not problems:
        return ValidationResult(
            passed=True,
            stage="reference_class",
            metrics=metrics,
            warnings=[
                "no reference was chosen, so the declared class '%s' was not checked against "
                "anything" % (declared_class or declared_layer)
            ],
        )
    return ValidationResult(passed=True, stage="reference_class", metrics=metrics, warnings=warnings)


# Phrases in a note that forbid something. A note attached to reference X that
# forbids X is self-contradictory, and that is exactly what shipped: a shallow
# asset whose note said the shallow reference "must NOT be chosen".
_PROHIBITION = re.compile(
    r"\b(must not|mustn't|do not|don't|never|shall not|should not|cannot|can't)\b[^.]*"
    r"\b(be chosen|be used|be selected|use|choose|select|attach|pick)\b",
    re.IGNORECASE,
)

# Words that turn a mention of a reference into an argument for it. Prose that
# merely names another reference ("overlaid on the stone base") is not an argument.
_CHOICE_VERB = re.compile(
    r"\b(chosen|choose|choosing|select|selected|use|used|using|prefer|preferred|"
    r"attach|attached|pick|picked)\b",
    re.IGNORECASE,
)

# "this is the same reference example_shallow_stone uses" -- a claim about another plan that
# the engine can check, because it has that plan.
_SAME_AS_PLAN = re.compile(
    r"\b(?:same|identical)\s+reference\s+(?:as|that)\s+(?:plan\s+)?[`\"']?([A-Za-z0-9_.\-]+)",
    re.IGNORECASE,
)
_SAME_AS_PLAN_ALT = re.compile(
    r"\b(?:the\s+)?reference\s+(?:that\s+)?([A-Za-z0-9_.\-]+)\s+uses\b",
    re.IGNORECASE,
)


def validate_reference_notes(
    references: list[object],
    *,
    plan_references: dict[str, list[str]] | None = None,
    available: list[object] | None = None,
    asset: str = "",
) -> ValidationResult:
    """Do the notes describe the reference they are attached to?

    Notes are free text, which is why a plan could attach the very reference its
    note forbade, and why a reason copied from another plan propagated a mistake
    into a document. Three concrete contradictions are checkable:

    * a note on reference X that NAMES a different reference -- it is describing
      something other than what it is attached to;
    * a note on X that forbids a reference which, by name or class, is X itself;
    * a note claiming "the same reference as <plan>" when that plan's actual
      references differ, which requires the sibling plans to be supplied.
    """
    problems: list[str] = []
    metrics: dict[str, float | int | str | bool] = {
        "notes_checked": 0,
        "plan_references_supplied": bool(plan_references),
    }
    names = [str(getattr(reference, "name", "")) for reference in references or []]
    pool_names = [str(getattr(item, "name", "")) for item in (available or [])]

    for reference in references or []:
        holder = str(getattr(reference, "name", ""))
        for note in getattr(reference, "notes", []) or []:
            text = str(note)
            metrics["notes_checked"] += 1

            # (a) naming ANOTHER reference as the one to use.
            #
            # Not "mentions another reference": a note on an ore reference
            # legitimately says its specks are "overlaid on the stone base", and
            # `stone` is another attached reference. Firing on that would punish
            # the notes that explain a composite choice. The dangerous note is the
            # one arguing for a DIFFERENT reference -- it names one and says to
            # choose/use/attach it -- because that note belongs on the other
            # reference and was copied here.
            if _CHOICE_VERB.search(text):
                for other in names + pool_names:
                    if other == holder or len(other) < 3:
                        continue
                    if re.search(
                        r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(other.lower()), text.lower()
                    ):
                        problems.append(
                            "reference '%s' carries a note that argues for '%s', a different "
                            "reference: the note names something else as the one to use, so it "
                            "describes something other than what it is attached to -- %r"
                            % (holder, other, text[:120])
                        )
                        break

            # (b) forbidding itself, by name or by LAYER.
            #
            # Only the layer, never the bare word "stone": every stone class
            # contains it, so matching on it made a correct deep note look like it
            # forbade a deep reference. The layer is the word that carries the
            # meaning -- "a shallow-layer stone must not be chosen", attached to a
            # deepslate, forbids nothing that is here.
            if _PROHIBITION.search(text):
                from .refclass import layer_of

                lowered = text.lower()
                holder_layer = layer_of(holder)
                self_references: list[str] = []
                if holder.lower() in lowered:
                    self_references.append("its own name")
                if holder_layer in {"shallow", "deep", "nether", "end"} and re.search(
                    r"\b%s" % holder_layer, lowered
                ):
                    self_references.append("its own layer ('%s')" % holder_layer)
                if self_references:
                    problems.append(
                        "reference '%s' carries a note that forbids it: the note says it must not "
                        "be chosen, and it refers to %s, which is this reference -- %r"
                        % (holder, " and ".join(sorted(set(self_references))), text[:160])
                    )

            # (c) a claim about another plan
            claimed = None
            for pattern in (_SAME_AS_PLAN, _SAME_AS_PLAN_ALT):
                found = pattern.search(text)
                if found:
                    claimed = found.group(1)
                    break
            if claimed and plan_references is not None:
                actual = plan_references.get(claimed) or plan_references.get(claimed.lower())
                if actual is None:
                    problems.append(
                        "reference '%s' claims to be the same as plan '%s', which was not supplied, "
                        "so the claim could not be checked -- %r" % (holder, claimed, text[:120])
                    )
                elif holder not in actual:
                    problems.append(
                        "reference '%s' claims to be the same as the one plan '%s' uses, but that "
                        "plan uses %s -- the claim is false"
                        % (holder, claimed, ", ".join(actual) or "nothing")
                    )

    metrics["contradictions"] = len(problems)
    if problems:
        return ValidationResult(
            passed=False, stage="reference_notes", metrics=metrics, errors=problems
        )
    return ValidationResult(passed=True, stage="reference_notes", metrics=metrics)


def validate_reference_pool(
    attached: list[str],
    available: list[str],
    *,
    asset: str,
    waiver: str = "",
    declared_class: str = "",
) -> ValidationResult:
    """Catch a same-class reference that was on disk and never attached.

    The engine could already list what a reference root holds, and it already
    recorded how many candidates a render was offered -- but nothing compared the
    two. So a delivery shipped an ore whose specks were hand-written while
    ``iron_ore.png`` sat in the same folder unused, and a raw ore with an empty
    reference list while ``raw_iron.png`` sat beside it. The user's reading,
    "this doesn't reference iron ore at all", was correct, and the evidence to
    say so was already on disk.

    The rule is deliberately narrow, because the engine must not pretend to know
    what an asset is:

    * every candidate in the pool is scored by how many content words its name
      shares with the asset's name;
    * the best **attached** score is the bar;
    * a candidate that was **not** attached and scores **higher** than that bar
      is a same-class reference that was available and unused -- an error unless
      the caller states why.

    An equal score does not fire, which is what keeps a raw ore that attached
    ``raw_iron`` from also being told off about ``iron_ore``: both share one
    content word, and the choice between them is the caller's.

    A pool that was never supplied is reported as unchecked, not as clean, so a
    green run never implies more than it checked.
    """
    asset_tokens = _name_tokens(asset)
    metrics: dict[str, float | int | str | bool] = {
        "attached": len(attached),
        "available": len(available),
        "asset_tokens": ",".join(sorted(asset_tokens)),
    }
    if not available:
        return ValidationResult(
            passed=True,
            stage="reference_pool",
            metrics={**metrics, "checked": False},
            warnings=[
                "no reference pool was supplied, so 'a same-class reference exists but was "
                "not attached' was NOT checked (pass one to enable it)"
            ],
        )

    def score(name: str) -> int:
        return len(_name_tokens(name) & asset_tokens)

    from .refclass import classify, layer_of

    # Every candidate's class is printed, so a caller picks by KIND rather than by
    # "does the filename look similar". Without this the table is a word-overlap
    # score, and "deep" against "shallow" is not a word overlap.
    metrics["available_classes"] = ", ".join(
        "%s=%s" % (name, classify(name)) for name in available
    )
    metrics["attached_classes"] = ", ".join(
        "%s=%s" % (name, classify(name)) for name in attached
    )

    attached_scores = [score(name) for name in attached]
    best_attached = max(attached_scores) if attached_scores else -1
    attached_names = {str(name).lower() for name in attached}
    unused = [
        (name, score(name))
        for name in available
        if str(name).lower() not in attached_names and score(name) > best_attached
    ]
    # A same-CLASS candidate that was not attached is a stronger finding than a
    # similar name: it is the reference the asset's own kind calls for. This is
    # the upgrade the Lead asked for -- the criterion becomes "class", not "score".
    if declared_class:
        attached_classes = {classify(name) for name in attached}
        class_unused = [
            name
            for name in available
            if str(name).lower() not in attached_names
            and classify(name) == declared_class
            and declared_class not in attached_classes
        ]
        metrics["unused_same_class_kind"] = len(class_unused)
        metrics["unused_same_class_names"] = ",".join(sorted(class_unused))
        for name in sorted(class_unused):
            if not any(existing == name for existing, _value in unused):
                unused.append((name, score(name)))
    metrics["best_attached_score"] = best_attached
    metrics["unused_same_class"] = len(unused)
    metrics["unused_names"] = ",".join(name for name, _value in sorted(unused))
    metrics["checked"] = True
    reason = str(waiver or "").strip()

    if not unused:
        return ValidationResult(passed=True, stage="reference_pool", metrics=metrics)

    listed = ", ".join("%s (score %d)" % (name, value) for name, value in sorted(unused))
    if attached:
        headline = (
            "REFERENCE AVAILABLE BUT UNUSED: %s %s on this reference root and matched this asset "
            "better than anything the plan attached, but the plan did not attach %s"
            % (listed, "is" if len(unused) == 1 else "are", "it" if len(unused) == 1 else "them")
        )
    else:
        # Nothing was attached at all, so "better than" says nothing. Say the
        # plain fact instead: same-class references were sitting right there.
        headline = (
            "REFERENCE AVAILABLE BUT UNUSED: the plan attached nothing, and %s %s on this "
            "reference root and share a name with this asset"
            % (listed, "is" if len(unused) == 1 else "are")
        )
    if reason:
        return ValidationResult(
            passed=True,
            stage="reference_pool",
            metrics=metrics,
            warnings=[headline, "accepted because reference_waiver says: %s" % reason],
        )
    return ValidationResult(
        passed=False,
        stage="reference_pool",
        metrics=metrics,
        errors=[
            headline + ". Attach it to the plan's references, or say why it does not apply "
            "in descriptor.reference_waiver"
        ],
        warnings=["attached reference(s): %s" % (", ".join(attached) or "none")],
    )


# The strictness the engine considers the reference -- the values the shipped
# family uses and which vanilla's own art passes. A plan may declare something
# looser, but if its product only passes *because* of the relaxation, that is a
# finding rather than a pass.
ENGINE_LIMITS: dict[str, tuple[str, float]] = {
    "accent_edge_max": (">", 60.0),
    "accent_ramp_max_dominant_share": (">", 0.6),
    "accent_ramp_min_monotone": ("<", 0.6),
    "accent_ramp_min_levels": ("<", 3.0),
    "accent_motif_repeat_max": (">", 2.0),
    "accent_layout_min_size_cv": ("<", 0.15),
    "accent_layout_min_spacing_cv": ("<", 0.15),
    "band_maximum_isolated": (">", 0.1),
    "band_maximum_step": (">", 30.0),
}


def _measured_for(key: str, metrics: dict[str, object]) -> float | None:
    """Read the measured counterpart of a declared limit out of the style metrics."""
    source = {
        "accent_edge_max": "accent_edge_delta_p90",
        "accent_ramp_max_dominant_share": "accent_ramp_dominant_max",
        "accent_ramp_min_monotone": "accent_ramp_monotone_min",
        "accent_ramp_min_levels": "accent_ramp_levels_min",
        "accent_motif_repeat_max": "accent_motif_repeat",
        "accent_layout_min_size_cv": "accent_layout_size_cv",
        "accent_layout_min_spacing_cv": "accent_layout_spacing_cv",
        "band_maximum_isolated": "band_isolated_share",
        "band_maximum_step": "band_mean_neighbour_step",
    }.get(key)
    if source is None:
        return None
    value = metrics.get("style." + source, metrics.get(source))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _short(value: float) -> str:
    return ("%g" % value) if float(value) == int(value) else ("%.4g" % value)


def validate_declared_limits(appearance: object, metrics: dict[str, object]) -> ValidationResult:
    """Refuse to let a plan quietly loosen the ruler it is measured against.

    A highlight whose edge step measured 83 passed because the plan raised
    ``accent_edge_max`` from the family's 60 to 90, and nothing said so. The
    relaxation is allowed; doing it silently is not, because a gate the gated
    thing can widen is not a gate.

    Fires only when both halves hold: the declared limit is looser than the
    engine's reference value **and** the measured product would have failed at
    that reference value. A plan that relaxes a limit it comfortably meets is left
    alone; a plan that relaxes one it only just meets has to say why in
    ``appearance.threshold_waiver``.
    """
    declared: list[str] = []
    for key, (direction, reference) in ENGINE_LIMITS.items():
        value = getattr(appearance, key, None)
        measured = _measured_for(key, metrics)
        if value is None or measured is None:
            continue
        looser = value > reference if direction == ">" else value < reference
        fails_reference = measured > reference if direction == ">" else measured < reference
        if looser and fails_reference:
            declared.append(
                "%s: declared %s, engine reference %s, measured %s"
                % (key, _short(float(value)), _short(reference), _short(measured))
            )

    metrics_out: dict[str, float | int | str | bool] = {
        "relaxed_limits": len(declared),
        "relaxations": " | ".join(declared) if declared else "",
    }
    if not declared:
        return ValidationResult(passed=True, stage="limits", metrics=metrics_out)
    headline = "DECLARED LIMIT RELAXED: " + "; ".join(declared)
    waiver = str(getattr(appearance, "threshold_waiver", "") or "").strip()
    if waiver:
        return ValidationResult(
            passed=True,
            stage="limits",
            metrics=metrics_out,
            warnings=[headline, "accepted because threshold_waiver says: %s" % waiver],
        )
    return ValidationResult(
        passed=False,
        stage="limits",
        metrics=metrics_out,
        errors=[
            headline + ". The product passes only because the limit was widened; say why in "
            "appearance.threshold_waiver, or bring the declared value back to the engine's "
            "reference"
        ],
    )


class SemanticCritic(Protocol):
    def review(self, descriptor: ShapeDescriptor, compiled: CompiledGeometry) -> ValidationResult:
        """Review semantic recognisability without altering the geometry."""


@dataclass
class StructuralCritic:
    """Offline critic used when an LLM critic is unavailable.

    This is deliberately conservative: it verifies descriptor parts are present
    and exposes metrics. It never pretends to recognise an arbitrary noun from
    pixels; an optional model critic can add that judgement later.
    """

    def review(self, descriptor: ShapeDescriptor, compiled: CompiledGeometry) -> ValidationResult:
        metrics = geometry_metrics(compiled)
        errors: list[str] = []
        warnings = ["offline structural critic: semantic noun recognition was not requested from a model"]
        for part in descriptor.parts:
            count = int(metrics.get("%s_pixels" % part.id, 0))
            if part.required and not part.paint_only and count == 0:
                errors.append("descriptor requires missing part: %s" % part.id)
        return ValidationResult(
            passed=not errors,
            stage="semantic",
            metrics=metrics,
            errors=errors,
            warnings=warnings,
        )


def aggregate_results(results: Iterable[ValidationResult]) -> ValidationResult:
    results = list(results)
    metrics: dict[str, float | int | str | bool] = {}
    errors: list[str] = []
    warnings: list[str] = []
    for result in results:
        metrics.update({"%s.%s" % (result.stage, key): value for key, value in result.metrics.items()})
        errors.extend("[%s] %s" % (result.stage, item) for item in result.errors)
        warnings.extend("[%s] %s" % (result.stage, item) for item in result.warnings)
    return ValidationResult(
        passed=not errors,
        stage="aggregate",
        metrics=metrics,
        errors=errors,
        warnings=warnings,
    )
