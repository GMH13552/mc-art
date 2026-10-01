"""Command-line surface of the mc-art engine.

There is no model here. Every subcommand either scans an asset root, turns an
image into text, rasterises a plan, or measures the result. The judgement --
what the asset should look like, which references matter, whether the render is
any good -- belongs to the caller.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .asset_groups import CATEGORIES, build_catalogue
from .contracts import ReferenceAsset, ReferenceRole
from .evidence import _appearance_reference_evidence, shape_authority
from .group_index import GroupReferenceSource
from .planfile import plan_from_file
from .style import ITEM_ACCENT_GAP_MAX, ITEM_ACCENT_GAP_MIN, ORE_ACCENT_GAP_RANGE
from .project_settings import SourcePlan, plan_sources
from .reference_index import build_index


def _default_cache() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / ".cache" / "live"


def add_project_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags that make a project's own strategy file mean something.

    Kept in one place so every command that reads assets asks the same
    question of the same file.
    """
    parser.add_argument("--project", metavar="DIR",
                        help="a project directory with mc-art.settings.json; its strategy "
                             "(reference root, mod toggles, includeGenerated) decides the stack")
    parser.add_argument("--no-generated", action="store_true",
                        help="override the file: leave the project's own textures out of the references")
    parser.add_argument("--no-mods", action="store_true",
                        help="override the file: keep only the vanilla baseline")
    parser.add_argument("--verbose", action="store_true",
                        help="list every mod jar and the namespaces it contributes")
    parser.add_argument("--version", metavar="NAME",
                        help="when the reference root is a game directory holding several "
                             "versions, name the one to use")


def _plan_for(args: argparse.Namespace) -> tuple[SourcePlan, list[str]]:
    """Turn --project plus --source into a source plan, and say what it decided.

    Nothing is inferred when --project is absent: without it the explicit
    --source values are the whole truth, exactly as before this existed.
    """
    explicit = list(getattr(args, "source", None) or [])
    project = getattr(args, "project", None)
    no_generated = bool(getattr(args, "no_generated", False))
    no_mods = bool(getattr(args, "no_mods", False))
    if project is None:
        # No strategy file is named, so the explicit sources are the whole
        # truth -- identical to how this command behaved before it existed.
        return SourcePlan(explicit=[Path(item).expanduser().resolve() for item in explicit]), []
    plan = plan_sources(
        project,
        explicit,
        include_generated=False if no_generated else None,
        include_mods=False if no_mods else None,
        version=getattr(args, "version", None),
        verbose=bool(getattr(args, "verbose", False)),
    )
    lines = plan.describe()
    if no_generated or no_mods:
        lines.append("  override ->%s%s" % (
            " --no-generated" if no_generated else "", " --no-mods" if no_mods else ""))
    return plan, lines


def _index_vanilla(args: argparse.Namespace) -> int:
    output = Path(args.out or (Path(__file__).resolve().parents[1] / "references" / "index.json")).expanduser().resolve()
    cache_root = Path(args.cache or (output.parent / ".cache")).expanduser().resolve()
    index = build_index(args.source, cache_root, rebuild=args.rebuild, with_pixel_text=args.with_pixel_text)
    index.save(output)
    print("INDEX -> %s" % output)
    print("SOURCE -> %s (%s)" % (index.source.path, index.source.fingerprint))
    print("TEXTURES -> %d; REUSED -> %d; BUILT -> %d; ERRORS -> %d" % (
        len(index.entries),
        index.cache_stats.get("entries_reused", 0),
        index.cache_stats.get("entries_built", 0),
        len(index.errors),
    ))
    return 0


def _list_groups(args: argparse.Namespace) -> int:
    """Show the logical names resolvable from the given roots.

    The catalogue never decodes a texture, so this is a fast way to check that
    a block really is one name rather than its individual faces.
    """
    plan, plan_lines = _plan_for(args)
    for line in plan_lines:
        print(line)
    catalogue = build_catalogue(plan.stack())
    try:
        if args.extract:
            written = catalogue.extract(args.extract, args.to or "outputs/_groups")
            print("EXTRACT -> %s (%d texture(s))" % (args.extract, len(written)))
            for path in written:
                print("  %s" % path)
            return 0
        if args.json:
            print(json.dumps(catalogue.to_manifest(), ensure_ascii=False, indent=2))
            return 0
        stats = catalogue.stats
        print("ROOTS -> %s" % "; ".join(stats["roots"]))
        print("RESOURCE PATHS -> %d; MODELS RESOLVED -> %d" % (
            stats["resource_paths"], stats["models_resolved"]))
        groups = catalogue.select(
            category=args.category, namespace=args.namespace,
            contains=args.filter, minimum_textures=args.min_textures,
        )
        for group in groups[: args.limit]:
            print("  %s" % group.describe())
        if len(groups) > args.limit:
            print("  ... %d more (raise --limit or narrow --filter)" % (len(groups) - args.limit))
        print("SHOWN -> %d of %d selected (%d total)" % (
            min(len(groups), args.limit), len(groups), len(catalogue.groups)))
        print("BY CATEGORY -> %s" % ", ".join(
            "%s=%d" % (key, stats["groups"][key]) for key in CATEGORIES if stats["groups"].get(key)))
        print("NUMERIC FAMILIES -> %d (pulled in %d texture(s))" % (
            stats["numeric_families"], stats["family_textures_pulled_in"]))
        print("ORPHANS -> %d group(s) / %d texture(s) no model references" % (
            stats["orphan_groups"], stats["orphan_textures"]))
        return 0
    finally:
        catalogue.close()


def _style_anchors(
    catalogue,
    source: GroupReferenceSource,
    group,
    plan: SourcePlan,
    limit: int = 8,
) -> list[ReferenceAsset]:
    """The project's own already-drawn textures, offered as style anchors.

    A new block should look like the blocks this project drew before it, not
    like vanilla.  These are a different kind of reference from the baseline:
    they carry ``pixel_style`` and ``palette`` and never ``shape``, because the
    silhouette has to come from the geometry authority, and they are read
    straight off disk on every run -- editing or deleting one changes exactly
    this list, and leaves the game baseline untouched.
    """
    generated = {path.resolve() for path in plan.generated}
    if not generated:
        return []
    chosen = []
    budget = limit
    for candidate in catalogue.select(category=getattr(group, "category", None)):
        if candidate.asset_id == group.asset_id:
            continue
        owned = [texture for texture in candidate.textures
                 if (root := catalogue.root_for(texture.resource_path)) is not None
                 and root.path.resolve() in generated]
        if not owned:
            continue
        # A group can carry several faces (example_bone_block has two, example_grass
        # three), so the budget is spent on textures -- capping groups let 8
        # groups arrive as 11 images.
        keep = min(len(owned), budget)
        if keep == 0:
            break
        chosen.append((candidate, keep))
        budget -= keep
    out: list[ReferenceAsset] = []
    for candidate, keep in chosen:
        try:
            entry = source.entry_for(candidate.asset_id)
            made = source.planning_assets(
                entry,
                [ReferenceRole.PIXEL_STYLE, ReferenceRole.PALETTE],
                display_name=candidate.name,
                generated_roots=generated,
            )
        except (KeyError, FileNotFoundError, ValueError):
            # Deleted between the catalogue scan and this read: dropping it is
            # what "keeps up with a project being edited" means.
            continue
        for asset in made[:keep]:
            out.append(ReferenceAsset(
                path=asset.path,
                name=asset.name,
                roles=list(asset.roles),
                notes=list(asset.notes) + [
                    "origin=generated",
                    "style_anchor=%s; already drawn in this project, match its palette and pixel style" % candidate.name,
                ],
                features=asset.features,
            ))
    return out


def _evidence(args: argparse.Namespace) -> int:
    """Everything a plan author needs about one asset, with no model call.

    The reference list, the role each carries, which member answers this
    request, and the literal pixel text of every small raster -- so the caller
    can write the descriptor, geometry and appearance itself.
    """
    plan, plan_lines = _plan_for(args)
    for line in plan_lines:
        print(line)
    if not plan.stack():
        print("ERROR: nothing to read assets from -- no usable --source, "
              "reference.directory resolved to no asset root, and the project's own "
              "pack is switched off. The lines above say which of those it was.",
              file=sys.stderr)
        return 2
    catalogue = build_catalogue(plan.stack())
    try:
        group = next((item for item in catalogue.select() if item.name == args.name), None)
        if group is None:
            names = sorted({item.name for item in catalogue.select()})
            close = [name for name in names if args.name.lower() in name.lower()][:10]
            print("ERROR: no logical asset named %r" % args.name, file=sys.stderr)
            if close:
                print("       did you mean: %s" % ", ".join(close), file=sys.stderr)
            return 1
        source = GroupReferenceSource(catalogue, args.cache or _default_cache())
        entry = source.entry_for(group.asset_id)
        assets = source.planning_assets(
            entry,
            [ReferenceRole.SHAPE, ReferenceRole.PIXEL_STYLE],
            display_name=args.name,
            preferred_member=args.member,
            max_frames=args.max_frames,
            generated_roots={path.resolve() for path in plan.generated},
        )
        # One frame answers this request; its siblings are context. Printing
        # every frame as 'shape' is what let a standby bow copy the half-drawn
        # frame's silhouette.
        authority = shape_authority(assets, args.member)
        if authority is not None:
            marked = []
            for asset in assets:
                if asset is authority:
                    roles = list(asset.roles)
                    notes = list(asset.notes) + ["shape_authority=this request; copy this silhouette"]
                else:
                    roles = [r for r in asset.roles if r is not ReferenceRole.SHAPE] or [ReferenceRole.PIXEL_STYLE]
                    notes = list(asset.notes) + [
                        "shape_authority=context only; another state of the same object, "
                        "not this request's silhouette"
                    ]
                marked.append(ReferenceAsset(
                    path=asset.path, name=asset.name, roles=roles, notes=notes, features=asset.features,
                ))
            assets = marked
        anchors = _style_anchors(catalogue, source, group, plan)
        if anchors:
            # Appended after the shape authority was chosen, so a style anchor
            # can never be mistaken for the silhouette this request must copy.
            assets = assets + anchors
        if args.json:
            print(json.dumps({
                "asset_id": group.asset_id,
                "member": args.member,
                "reference_count": len(assets),
                "references": [
                    {
                        "name": asset.name,
                        "path": asset.path,
                        "roles": [role.value for role in asset.roles],
                        "notes": list(asset.notes),
                        "size": [asset.features.get("width"), asset.features.get("height")],
                    }
                    for asset in assets
                ],
            }, ensure_ascii=False, indent=2))
            return 0
        print("ASSET -> %s (%d reference(s))" % (group.asset_id, len(assets)))
        print()
        print(_appearance_reference_evidence(assets))
        return 0
    finally:
        catalogue.close()


class _OfflineCritic:
    """No model: a plan handed to 'render' is already judged by its author."""

    repair_soft_failures = False

    def review(self, descriptor, compiled):
        from .validation import ValidationResult

        return ValidationResult(
            passed=True,
            stage="offline_render",
            metrics={"semantic_review_skipped": True},
            warnings=["render executes a plan; no semantic model was consulted"],
        )


def _render(args: argparse.Namespace) -> int:
    """Execute a plan: the deterministic half of the pipeline, alone.

    Every decision is already frozen into the plan file, so this needs no
    credentials, no network and no judgement.
    """
    from .pipeline import GenerationPipeline
    from .planfile import reference_pool

    plan = plan_from_file(args.plan)
    if args.reference_pool:
        # Whatever sat on the reference root, attached or not. Without a pool the
        # "you had iron_ore.png and did not attach it" check cannot run, and the
        # render says so rather than implying it looked.
        pool = []
        for directory in args.reference_pool:
            pool.extend(reference_pool(directory))
        plan = replace(plan, available_references=pool)
    result = GenerationPipeline(
        critic=_OfflineCritic(), repairer=None, max_geometry_repairs=0,
    ).run(plan, args.out, package=not args.no_package)
    print("RENDER -> %s" % Path(args.out).resolve())
    print("REQUEST -> %s (%dx%d)" % (
        plan.request.name or plan.request.query, plan.request.width, plan.request.height))
    print("PARTS -> %s" % ", ".join(part.id for part in plan.descriptor.parts))
    print("SPRITE -> %s" % result.sprite_path)
    print("VALIDATION -> %s" % ("passed" if result.validation.passed else "FAILED"))
    for error in list(result.validation.errors)[:5]:
        print("  error: %s" % error)
    style = result.validation.metrics
    if "style.accent_pixels" in style:
        budget = style.get("style.accent_budget", -1)
        print("STYLE -> accent=%s/%s band=%s isolated=%.2f%% edge_p90=%s (%s)" % (
            style.get("style.accent_pixels"),
            "unchecked" if budget == -1 else budget,
            style.get("style.band_count"),
            float(style.get("style.band_isolated_share", 0.0)) * 100.0,
            style.get("style.accent_edge_delta_p90"),
            style.get("style.band_verdict"),
        ))
    report_path = Path(args.out) / "reference_selection.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        chosen = report.get("chosen") or {}
        if chosen:
            print("REFERENCE -> %s (%s) because %s" % (
                chosen.get("name"), chosen.get("mode"), report.get("reason")))
        else:
            print("REFERENCE -> none used, at step '%s': %s" % (
                report.get("step"), report.get("step_detail")))
    return 0 if result.sprite_path and result.validation.passed else 1


def _refclass(args: argparse.Namespace) -> int:
    """Print the class table, classify the names given, or audit a plans directory.

    The table is printed because it is a published rule, not an implementation
    detail: if the engine's idea of "deep" differs from yours, you should be able
    to read it rather than infer it from a failed gate.
    """
    from .planfile import plan_from_file, sibling_references
    from .refclass import classify, describe_layers, describe_table, layer_of

    print("REFERENCE CLASS TABLE (first match wins)")
    for name, patterns in describe_table():
        print("  %-14s %s" % (name, patterns))
    print()
    print("LAYER RULES")
    for name, patterns in describe_layers():
        print("  %-14s %s" % (name, patterns))
    print("  %-14s (everything else follows from its class)" % "class default")
    print()

    failures = 0
    for value in args.names:
        print("  %-24s class=%-14s layer=%s" % (value, classify(value), layer_of(value)))

    if args.plans:
        directory = Path(args.plans)
        print()
        print("PLANS IN %s" % directory)
        print("  %-24s %-22s %-14s %s" % ("plan", "attached reference", "class", "layer"))
        for plan_path in sorted(directory.glob("*.plan.json")):
            plan = plan_from_file(plan_path)
            plan_name = plan_path.name[: -len(".plan.json")]
            declared = plan.descriptor.reference_class or "-"
            references = plan.references or []
            if not references:
                print("  %-24s %-22s %-14s %s" % (plan_name, "(none)", declared, "-"))
                failures += 1
                continue
            # The declared class must be REPRESENTED among the references, not
            # carried by each one: an ore's base rock is legitimately a different
            # class, so flagging every base reference would cry wolf on a correct
            # plan.
            represented = any(
                classify(reference.name) == plan.descriptor.reference_class
                for reference in references
            )
            for reference in references:
                derived = classify(reference.name)
                print("  %-24s %-22s %-14s %s" % (
                    plan_name, reference.name, derived, layer_of(reference.name)))
            if plan.descriptor.reference_class and not represented:
                print("  %-24s %s" % (
                    "", "<-- declares %s, and NO attached reference is one"
                    % plan.descriptor.reference_class))
                failures += 1
        print()
        print("family view (declared class per plan) -- a plan whose attached references do not")
        print("include the class it declares is the outlier, and it is marked:")
        for plan_path in sorted(directory.glob("*.plan.json")):
            plan = plan_from_file(plan_path)
            plan_name = plan_path.name[: -len(".plan.json")]
            attached = ", ".join(
                "%s=%s" % (reference.name, classify(reference.name))
                for reference in plan.references
            ) or "(none)"
            mark = ""
            if plan.descriptor.reference_class and plan.references and not any(
                classify(reference.name) == plan.descriptor.reference_class
                for reference in plan.references
            ):
                mark = "   <-- OUTLIER: declares %s, attaches none" % plan.descriptor.reference_class
            print("  %-24s declared=%-14s attached=%s%s" % (
                plan_name, plan.descriptor.reference_class or "-", attached, mark))
    return 1 if failures else 0


def _doctor(args: argparse.Namespace) -> int:
    """One command for every environment question that has bitten this project."""
    from .doctor import main as doctor_main

    return doctor_main([])


def _gap_from_refs(args: argparse.Namespace) -> int:
    """Measure the embedding band from references, rather than guessing at it.

    The same measurement as `scripts/measure-accent-gap.py`, reachable where the
    error message points. Two figures, deliberately: the per-deposit range is what
    `accent_base_gap_min/max` means, and the per-pixel range is the sharper
    boundary figure. Averaging a whole deposit into one number first is how the
    engine's own 12.24 was produced -- a middling value that then mis-measured
    every ore.
    """
    import importlib.util
    import json as _json
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "measure-accent-gap.py"
    if not script.exists():
        print("gap-from-refs: %s is not present in this installation" % script, file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location("measure_accent_gap", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    paths = [Path(item) for item in args.textures]
    if args.dir:
        paths.extend(sorted(Path(args.dir).glob("*.png")))
    if not paths:
        print("gap-from-refs: give textures or --dir", file=sys.stderr)
        return 2

    rows = [module.measure(path) for path in paths if path.exists()]
    if args.json:
        print(_json.dumps(rows, indent=2))
        return 0

    deposits: list[float] = []
    print("%-16s %-8s %-30s %s" % (
        "reference", "base", "PER DEPOSIT (declare this)", "PER PIXEL (boundary)"))
    for row in rows:
        if "per_deposit" not in row:
            print("%-16s %-8s %s" % (row["name"], row.get("base_luma_mean", "-"), row.get("note", "?")))
            continue
        dep, pix = row["per_deposit"], row["per_pixel"]
        deposits.extend([dep["min"], dep["max"]])
        print("%-16s %-8s %+7.1f .. %+7.1f (med %+.1f)   %+7.1f .. %+7.1f (n=%d)" % (
            row["name"], row["base_luma_mean"], dep["min"], dep["max"], dep["median"],
            pix["min"], pix["max"], pix["count"]))
    if deposits:
        print()
        print("Declare these -- per deposit, which is what accent_base_gap_min/max means:")
        print('  "accent_base_gap_min": %+.1f,' % min(deposits))
        print('  "accent_base_gap_max": %+.1f,' % max(deposits))
        print()
        print("The engine's default (%.1f..%.1f) is the ITEM band: one item's accent, averaged."
              % (ITEM_ACCENT_GAP_MIN, ITEM_ACCENT_GAP_MAX))
        print("The eight vanilla ores run %.1f to %.1f per deposit, none of them inside it."
              % ORE_ACCENT_GAP_RANGE)
        print("Mask definition matters: this uses a local-median chroma rule, so a different")
        print("mask gives different numbers. Declare from your own measurement and say which.")
    return 0


def _why_reference(args: argparse.Namespace) -> int:
    """Say which reference will paint this canvas, and what the others scored.

    "深渊原石参考的是浅层原石" is a question about the selection, so the answer is
    the selection's own candidate table -- including the entries that were
    rejected, and the rule that rejected them.
    """
    from .appearance import reference_selection_report
    from .geometry import compile_geometry

    plan = plan_from_file(args.plan)
    mask = None
    try:
        mask = compile_geometry(plan.geometry).mask
    except (KeyError, TypeError, ValueError):
        mask = None
    report = reference_selection_report(
        plan.references, plan.request.width, plan.request.height, target_mask=mask
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print("CANVAS -> %dx%d" % (report["canvas"][0], report["canvas"][1]))
    counts = report.get("counts") or {}
    print("OFFERED -> %s; READABLE -> %s; ELIGIBLE -> %s" % (
        counts.get("offered"), counts.get("readable"), counts.get("eligible")))
    chosen = report.get("chosen")
    print("CHOSEN -> %s" % (
        "%s [%s] mode=%s score=%s" % (
            chosen["name"], ",".join(chosen["roles"]), chosen["mode"], chosen["score"])
        if chosen else "nothing usable"))
    print("STEP   -> %s" % report.get("step"))
    print("WHY    -> %s" % report["reason"])
    print("CANDIDATES ->")
    for row in report["candidates"]:
        print("  %-28s %-24s score=%-8s %s" % (
            row["name"],
            ",".join(row["roles"]) or "-",
            row["score"],
            row["reason"] if not row["eligible"] else "eligible (alpha_overlap=%s)" % row["alpha_overlap"],
        ))
    return 0


def _audit(args: argparse.Namespace) -> int:
    """Measure what a rendered sprite actually came out like, and gate on it.

    Every number here is an answer to something a person said by hand:
    "不能是这么难看的点点" is the accent cluster sizes, "突兀的来一两个点" is the
    budget and the isolated-pixel share, "橙色和蓝色的边缘要拖突兀有多突兀" is the
    accent-to-base step, and "你能看出两个本来是一个东西的吗" is the family axis
    span. Nothing is judged unless the caller declares the budget.
    """
    from .style import accent_audit, band_report, family_axes, family_axes_summary

    accent_colors = list(args.accent_color or [])
    base_colors = list(args.base_color or [])
    audit = {
        "accent_colors": accent_colors,
        "base_colors": base_colors,
        "accent_budget": args.accent_budget,
        "minimum_cluster": args.min_cluster,
        "accent_edge_max": args.accent_edge_max,
        "band_maximum_isolated": args.max_isolated,
        "band_maximum_step": args.max_step,
        "sprites": [
            {
                "bands": band_report(
                    sprite, maximum_isolated=args.max_isolated, maximum_step=args.max_step
                ),
                "accent": accent_audit(
                    sprite,
                    accent_colors=accent_colors,
                    base_colors=base_colors,
                    budget=args.accent_budget,
                    minimum_cluster=args.min_cluster,
                    edge_max=args.accent_edge_max,
                ),
            }
            for sprite in args.sprites
        ],
    }
    if len(args.sprites) > 1:
        audit["family"] = family_axes(
            args.sprites,
            accent_colors=accent_colors,
            base_colors=base_colors,
            maximum_hue_span_deg=args.max_hue_span,
            maximum_luma_span=args.max_value_span,
            maximum_accent_hue_span_deg=args.max_accent_hue_span,
        )
    if args.json:
        print(json.dumps(audit, ensure_ascii=False, indent=2))
    else:
        for sprite, row in zip(args.sprites, audit["sprites"]):
            bands, accent = row["bands"], row["accent"]
            print("%s" % sprite)
            print("  bands   -> %d band(s)  largest_flat=%.0f%%  isolated=%.2f%%  step=%.1f  %s" % (
                bands["band_count"], bands["largest_flat_share"] * 100.0,
                bands["isolated_share"] * 100.0, bands["mean_neighbour_step"], bands["verdict"]))
            print("  accent  -> %d pixel(s) (%s) at hue %s deg, mean value %s, in %d cluster(s)  smallest=%d  below_min=%d" % (
                accent["accent_pixels"], accent["detection"], accent["accent_hue_degrees"],
                accent["accent_luma_mean"], accent["clusters"],
                accent["smallest_cluster"], accent["below_minimum_pixels"]))
            print("  edge    -> mean=%s p90=%s (limit %s)" % (
                accent["edge_delta_mean"], accent["edge_delta_p90"], accent["edge_max"]))
            for reason in accent["reasons"]:
                print("  FAIL    -> %s" % reason)
            for reason in bands["reasons"]:
                print("  FAIL    -> %s" % reason)
        if "family" in audit:
            print(family_axes_summary(audit["family"]))
            for reason in audit["family"]["reasons"]:
                print("  FAIL    -> %s" % reason)
    failed = any(not row["accent"]["consistent"] for row in audit["sprites"])
    failed = failed or any(row["bands"].get("consistent") is False for row in audit["sprites"])
    if "family" in audit and args.family:
        failed = failed or not audit["family"]["consistent"]
    return 1 if failed else 0


def _block(args: argparse.Namespace) -> int:
    """Emit a multi-box block model with real per-face UVs, and look at it.

    A block entity -- a desk with a sheet on it, a lectern, an altar -- is not a
    recoloured plank. This command writes the elements, refuses a face whose UV
    would stretch a foreign texture across it, and renders the model in the
    game's own camera so the sheet can be seen to be a sheet.
    """
    from .blockmodel import render_block_spec, write_block_model

    spec_path = Path(args.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    result = write_block_model(
        spec, args.out, base_dir=spec_path.parent, pack_format=args.pack_format
    )
    report = result["report"]
    print("MODEL    -> %s" % result["model"])
    print("KIND     -> %s; elements=%d faces=%d; textures=%s" % (
        report["kind"], report["elements"], report["faces"], ", ".join(report["textures"])))
    if report["unused_textures"]:
        print("  NOTE   unused texture(s): %s" % ", ".join(report["unused_textures"]))
    for row in report["faces_detail"]:
        print("  %-14s %-6s %-10s uv=%-16s world=%sx%s  %s" % (
            row["element"], row["face"], row.get("texture"),
            row.get("uv") or "-", row["world_extent"][0], row["world_extent"][1],
            row["reason"]))
    for problem in report["problems"][:12]:
        print("  PROBLEM %s" % problem)
    for note in report["notes"][:8]:
        print("  note    %s" % note)
    print("UV_MAP   -> %s" % result["uv_map"])
    if args.render and report["passed"]:
        views = tuple(args.view or ("front34", "side"))
        rendered = render_block_spec(spec, args.out, args.render, views=views)
        print("VIEW     -> %s (%s)" % (rendered["image"], ", ".join(views)))
    print("VERDICT  %s" % ("PASS" if report["passed"] else "FAIL"))
    return 0 if report["passed"] else 1


def _measure(args: argparse.Namespace) -> int:
    """Deterministic frame metrics for a set of sprites."""
    from .metrics import frame_continuity

    report = frame_continuity(args.sprites, baseline_sprites=args.baseline or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _pack(args: argparse.Namespace) -> int:
    """Assemble a multi-texture asset: several plans, one face-correct pack.

    One plan is one 16x16 texture. A log needs two -- end grain and side -- and
    they are different files, so a pack built one plan at a time can only emit
    cube_all. The manifest names the textures and declares the block model.
    """
    from .pack import build_pack

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    result = build_pack(manifest, args.out)
    print("PACK -> %s" % result["pack"])
    print("NAMESPACE -> %s" % result["namespace"])
    print("TEXTURES -> %d" % len(result["textures"]))
    for block in result["blocks"]:
        print("  block %s" % block)
    for warning in result["warnings"]:
        print("  WARNING: %s" % warning)
    for preview in result["previews"]:
        print("  preview %s" % preview)
    print("FACE MAP -> %s" % (Path(result["pack"]) / "FACE_MAP.txt"))
    return 0


def _layouts(args: argparse.Namespace) -> int:
    """Which entity shapes already exist, so one can be found rather than invented."""
    from .uv_tools import list_layouts

    found = list_layouts(args.root)
    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    if not found:
        print("no layouts in %s" % (args.root or "layouts/"))
        return 1
    for entry in found:
        print("%s  %sx%s  %d box(es)" % (
            entry["name"], entry["canvas"][0], entry["canvas"][1], entry["boxes"]))
        print("    parts: %s" % ", ".join(entry["parts"]))
        if entry["notes"]:
            print("    %s" % entry["notes"])
        print("    %s" % entry["path"])
    print()
    print("No shape that fits? Author one:  mc-art boxes --spec boxes.json --out my_layout.json")
    return 0


def _boxes(args: argparse.Namespace) -> int:
    """Derive a UV layout from a box decomposition the caller wrote."""
    import json as _json

    from .uv_tools import layout_from_boxes

    spec = _json.loads(Path(args.spec).read_text(encoding="utf-8"))
    result = layout_from_boxes(spec, args.out, canvas_width=args.canvas, margin=args.margin)
    print("LAYOUT -> %s" % result["layout"])
    print("CANVAS -> %sx%s" % (result["canvas"][0], result["canvas"][1]))
    print("BOXES  -> %d  parts: %s" % (len(result["boxes"]), ", ".join(result["parts"])))
    print("REGIONS -> %d" % result["regions"])
    print("NEXT -> mc-art uv --layout %s --texture <atlas.png> --out <dir>" % result["layout"])
    return 0


def _uv(args: argparse.Namespace) -> int:
    """Render an entity atlas against its layout so it can actually be seen."""
    from .uv_tools import render_views

    result = render_views(args.layout, args.texture, args.out, scale=args.scale)
    for key, value in result["views"].items():
        print("%-14s %s" % (key.upper(), value))
    for warning in result["warnings"]:
        print("WARNING  %s" % warning)
    if any(key.endswith("_error") for key in result["views"]):
        print("one preview could not be built; the uvmap alone still shows the region ownership")
    return 0


def _model_from_java(args: argparse.Namespace) -> int:
    """Read a hand-written or generated model class back into a spec."""
    from .modjava import class_name_from, parse

    source = Path(args.java).read_text(encoding="utf-8")
    name = class_name_from(args.java)
    spec = parse(source, name=name)
    boxes = sum(len(part["boxes"]) for part in spec["parts"])
    print("JAVA   %s -> %d part(s) %d box(es)" % (args.java, len(spec["parts"]), boxes))
    for part in spec["parts"]:
        for box in part["boxes"]:
            print("  %-12s uv=(%3d,%3d) whd=%d,%d,%d  at=%s" % (
                part["name"], box["u"], box["v"], box["w"], box["h"], box["d"],
                tuple(box["at"])))
    if args.emit_spec:
        Path(args.emit_spec).write_text(json.dumps(spec, indent=1) + chr(10), encoding="utf-8")
        print("SPEC   -> %s" % args.emit_spec)
        print("NEXT   -> mc-art entity --spec %s --out <dir>" % args.emit_spec)
    return 0


def _model(args: argparse.Namespace) -> int:
    """Read an entity model out of compiled game code instead of guessing it."""
    from .vanilla_model import (JarSource, boxes_for_class, boxes_for_texture,
                                layout_document, models_for_texture)

    if args.java:
        return _model_from_java(args)
    if not args.jar:
        print("name a source: --jar for a compiled model, or --java for a source file")
        return 1

    with JarSource(args.jar) as source:
        # the archive-wide modes answer without naming a texture or a class
        if args.list_models or args.list or args.all or args.all_models:
            if args.all_models:
                return 0 if _emit_all_models(source, args) else 1
            if args.all:
                return 0 if _emit_all(source, args) else 1
            if args.list_models:
                return _list_models(source, args)
            return _list_textures(source, args)
        if not args.texture and not args.model_class:
            print("name something: --texture, --model-class, --list, --list-models, --all or --all-models")
            return 1
        if args.texture:
            models = models_for_texture(source, args.texture)
            if not models:
                print("NO MODEL  no class in %s draws %s" % (args.jar, args.texture))
                return 1
            boxes = boxes_for_texture(source, args.texture)
            label = args.texture
        else:
            models = [args.model_class]
            boxes = boxes_for_class(source.text, args.model_class)
            label = args.model_class
        print("MODEL  %s -> %s" % (label, ", ".join(models)))
        if not boxes:
            return 1
        for box in boxes:
            net = box.net_size
            print("  %-8s uv=(%3d,%3d) whd=%d,%d,%d  net=%dx%d  delta=%g%s" % (
                box.part, box.u, box.v, box.w, box.h, box.d, net[0], net[1], box.delta,
                ("  rot=%s" % (box.rotation,)) if box.rotation else ""))
        if not boxes:
            print("  (no boxes: the model may be built by a class this reader did not reach)")
        if not boxes:
            return 1
        width = args.texture_width
        height = args.texture_height
        if args.texture and (width is None or height is None):
            found = _texture_size(source, args.texture)
            if found:
                width, height = found
        if args.all_models:
            written = _emit_all_models(source, args)
            return 0 if written else 1
        if args.all:
            written = _emit_all(source, args)
            return 0 if written else 1
        if args.out:
            document = layout_document(
                boxes, name=Path(args.out).stem, source="%s (%s)" % (label, ", ".join(models)),
                texture_width=width or 64, texture_height=height or 32)
            Path(args.out).write_text(json.dumps(document, indent=1) + chr(10), encoding="utf-8")
            print("LAYOUT -> %s" % args.out)
            print("NEXT   -> mc-art uv --layout %s --texture <%s> --out <dir>" % (args.out, label))
        if args.emit_spec:
            from .vanilla_model import render_spec

            model_class = args.model_class or models[0]
            spec = render_spec(source.text, model_class, texture=args.texture,
                               tex_size=(width or 64, height or 32))
            Path(args.emit_spec).write_text(json.dumps(spec, indent=1) + chr(10), encoding="utf-8")
            print("SPEC   -> %s" % args.emit_spec)
            print("PARTS  -> %s" % ", ".join(part["name"] for part in spec["parts"]))
            for part in spec["parts"]:
                if part["rot"]:
                    print("POSE   -> %s %s" % (part["name"], part["rot"]))
            print("NEXT   -> mc-art ingame --model %s --source <same jar> --out <dir>" % args.emit_spec)
    return 0


def _texture_size(source, relative: str):
    """Find a texture in the jar and read its pixel size, any namespace."""
    blob = source.texture_bytes(relative)
    if blob is None:
        return None
    import io

    from PIL import Image

    return Image.open(io.BytesIO(blob)).size


def _entity(args: argparse.Namespace) -> int:
    """Plan an entity: one description in, a layout and a render spec out."""
    from .entity import audit, format_audit, plan, write_plan

    payload = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        result = plan(payload, canvas_width=args.canvas_width)
    except ValueError as error:
        print("SPEC ERROR  %s" % error)
        return 1
    written = write_plan(result, args.out)
    spec = result["spec"]
    print("MODEL   %s  %dx%d" % (spec["name"], result["canvas"][0], result["canvas"][1]))
    for part in spec["parts"]:
        boxes = " ".join("uv(%d,%d) %dx%dx%d" % (box["u"], box["v"], box["w"], box["h"], box["d"])
                         for box in part["boxes"])
        print("  %-12s pivot=%-16s rot=%-14s %s" % (
            part["name"], tuple(_trim_all(part["pivot"])), part["rot"] or "{}", boxes))
    print("LAYOUT  -> %s" % written["layout"])
    print("SPEC    -> %s" % written["model"])
    if args.java:
        from .modjava import emit

        class_name = args.class_name or _model_class_name(spec["name"])
        source = emit(spec, class_name=class_name, package=args.package or "",
                      texture=spec.get("texture"))
        target = Path(args.java)
        if target.suffix != ".java":
            target = target / (class_name + ".java")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        print("JAVA    -> %s" % target)
        print("          the addBox calls in it are the rectangles above, so the")
        print("          atlas and the mod cannot drift; --java reads it back")
    if args.atlas:
        from PIL import Image

        report = audit(spec, Image.open(args.atlas))
        print("ATLAS   %s" % format_audit(report))
        for box in report["boxes"]:
            if box["empty"]:
                print("  EMPTY  %-14s %d of %d texels unpainted" % (box["id"], box["empty"], box["cells"]))
        if report["stray_pixels"]:
            print("  STRAY  first pixels: %s" % report["stray_pixels"][:12])
        if report["stray"]:
            print("VERDICT FAIL  %d opaque texel(s) land outside every box; the game never samples them"
                  % report["stray"])
            return 1
        print("VERDICT PASS  every opaque texel lands inside a box")
        return 0
    print("NEXT    -> paint a %dx%d atlas, then:" % (result["canvas"][0], result["canvas"][1]))
    print("           mc-art uv --layout %s --texture <atlas.png> --out <dir>" % written["layout"])
    print("           mc-art entity --spec %s --out %s --atlas <atlas.png>" % (args.spec, args.out))
    print("           mc-art ingame --model %s --texture <atlas.png> --out <view.png>" % written["model"])
    return 0


def _trim_all(values):
    out = []
    for value in values:
        number = float(value)
        out.append(int(number) if number == int(number) else number)
    return out


def _model_class_name(name: str) -> str:
    cleaned = "".join(part.capitalize() for part in str(name).replace("-", "_").split("_") if part)
    return "Model" + (cleaned or "Entity")


def _list_models(source, args) -> int:
    from .vanilla_model import UnsupportedBytecode, boxes_for_class

    rows = []
    for cls in source.model_classes():
        if args.filter and args.filter not in cls:
            continue
        try:
            boxes = boxes_for_class(source.text, cls)
        except (KeyError, UnsupportedBytecode, ValueError):
            boxes = []
        if boxes:
            rows.append((cls, len({box.part for box in boxes}), len(boxes)))
    for cls, parts, boxes in rows:
        print("%-64s %2d part(s) %3d box(es)" % (cls, parts, boxes))
    print()
    print("%d model class(es). Next: --model-class <one> --emit-spec FILE" % len(rows))
    return 0


def _list_textures(source, args) -> int:
    from .vanilla_model import UnsupportedBytecode, boxes_for_texture, models_for_texture

    rows = []
    for relative in source.textures(filter_text=args.filter):
        found = models_for_texture(source, relative)
        if not found:
            continue
        try:
            count = len(boxes_for_texture(source, relative))
        except (KeyError, UnsupportedBytecode, ValueError):
            count = 0
        if count:
            rows.append((relative, ", ".join(found), count))
    for relative, model, count in rows:
        print("%-52s %-16s %2d box(es)" % (relative, model, count))
    print()
    print("%d texture(s) with a model. Next: --all --out DIR" % len(rows))
    return 0


def _safe_name(relative: str) -> str:
    """A texture path as one flat filename, so a whole project fits in a folder."""
    stem = relative[:-4] if relative.endswith(".png") else relative
    for prefix in ("textures/entity/", "textures/entities/", "textures/"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem.replace("/", "__").replace(" ", "_")


def _emit_all(source, args) -> int:
    """Emit a render spec for every texture in the archive that has a model."""
    from .vanilla_model import UnsupportedBytecode, render_spec

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = failed = 0
    for relative in source.textures(filter_text=args.filter):
        found = models_for_texture(source, relative)
        if not found:
            continue
        size = _texture_size(source, relative) or (64, 32)
        try:
            spec = render_spec(source.text, found[0], texture=relative, tex_size=size)
        except (KeyError, UnsupportedBytecode, ValueError) as error:
            print("  SKIP   %-46s %s" % (relative, error))
            failed += 1
            continue
        if not spec["parts"]:
            failed += 1
            continue
        target = out / (_safe_name(relative) + ".json")
        target.write_text(json.dumps(spec, indent=1) + chr(10), encoding="utf-8")
        parts = len(spec["parts"])
        boxes = sum(len(part["boxes"]) for part in spec["parts"])
        pose = ", ".join("%s %s" % (part["name"], part["rot"])
                         for part in spec["parts"] if part["rot"])
        print("  SPEC   %-46s %-14s %2d part(s) %2d box(es) %sx%s %s" % (
            relative, spec["renderer_class"], parts, boxes, size[0], size[1], pose))
        written += 1
    print()
    print("%d spec(s) written to %s, %d texture(s) had no reachable model"
          % (written, out, failed))
    if written:
        print("NEXT -> mc-art ingame --model %s --source <same source> --out <dir>"
              % (out / (_safe_name(source.textures(filter_text=args.filter)[0]) + ".json")))
    return written


def _emit_all_models(source, args) -> int:
    """Emit a spec for every model class the archive builds, texture unknown.

    This is the route for a project whose textures are registered at runtime:
    the models are findable by shape, and the caller pairs a texture with each
    spec by passing --texture when they render it.
    """
    from .vanilla_model import UnsupportedBytecode, render_spec

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for cls in source.model_classes():
        if args.filter and args.filter not in cls:
            continue
        try:
            spec = render_spec(source.text, cls)
        except (KeyError, UnsupportedBytecode, ValueError):
            continue
        if not spec["parts"]:
            continue
        target = out / (cls.replace(".", "__") + ".json")
        target.write_text(json.dumps(spec, indent=1) + chr(10), encoding="utf-8")
        boxes = sum(len(part["boxes"]) for part in spec["parts"])
        print("  SPEC   %-60s %2d part(s) %3d box(es)" % (cls, len(spec["parts"]), boxes))
        written += 1
    print()
    print("%d spec(s) written to %s (no texture recorded; pass --texture when rendering)"
          % (written, out))
    return written


def _ingame(args: argparse.Namespace) -> int:
    """Render the game's own view of a model, so a delivery can be looked at."""
    from .ingame import (CONTROLS, VIEWS, Camera, TextureSource, fit_camera,
                         load_block_model, render_block_model, render_entity,
                         render_views, selftest)

    viewport = (args.width, args.height)
    out = Path(args.out)

    if args.selftest:
        ok, detail, files = selftest(None, out, viewport=viewport)
        print("SELFTEST %s  %s" % ("PASS" if ok else "FAIL", detail))
        for name in files:
            print("GLCONTROL %s" % name)
        return 0 if ok else 1

    if args.control:
        names = sorted(CONTROLS) if "all" in args.control else args.control
        if not args.source:
            print("--control needs --source (the jar or asset root holding the textures)")
            return 1
        views = tuple(args.view) if args.view else ("front34", "side")
        with TextureSource(args.source) as source:
            for name in names:
                if name not in CONTROLS:
                    print("unknown control %s; have %s" % (name, ", ".join(sorted(CONTROLS))))
                    return 1
                for written in render_views(name, source, out, views=views, viewport=viewport):
                    print("CONTROL %s" % written)
        return 0

    if args.block:
        if not args.source:
            print("--block needs --source")
            return 1
        with TextureSource(args.source) as source:
            model = load_block_model(source, args.block)
            camera = Camera(_point(args.frm, (2.0, -0.55, 1.75)),
                            _point(args.at, (0.5, 0.5, 0.42)), viewport=viewport)
            print("BLOCK   %s" % render_block_model(model, source, out, camera))
        return 0

    if args.model:
        spec = json.loads(Path(args.model).read_text(encoding="utf-8"))
        if args.source:
            source = TextureSource(args.source)
            texture = spec.get("texture") or args.texture
        elif args.texture:
            path = Path(args.texture)
            source = TextureSource(path.parent)
            texture = path.name
        else:
            print("--model needs either --source or a --texture file path")
            return 1
        if not texture:
            print("the spec has no 'texture' key and no --texture was given, so there is "
                  "nothing to sample; --emit-spec writes the path for you")
            return 1
        try:
            from .entity import audit, format_audit, load_spec
            from .ingame import describe

            print("MODEL   %s" % describe(spec))
            try:
                report = audit(load_spec(spec), source.image(texture))
                print("ATLAS   %s" % format_audit(report))
                if report["stray"]:
                    print("        %d opaque texel(s) land outside every box"
                          % report["stray"])
            except ValueError as error:
                print("ATLAS   (not checked: %s)" % error)
            view = args.view[0] if args.view else "front34"
            camera = fit_camera(spec, VIEWS[view], viewport=viewport)
            print("ENTITY  %s" % render_entity(
                [(spec, texture, (1.0, 1.0, 1.0))], source, out, camera))
        finally:
            source.close()
        return 0

    print("nothing to render: pass --selftest, --control, --block or --model")
    return 1


def _point(text, default):
    if not text:
        return default
    parts = [float(value) for value in text.split(",")]
    if len(parts) != 3:
        raise SystemExit("a camera point needs three comma separated numbers")
    return tuple(parts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc_art",
        description="Deterministic Minecraft art engine. Scan, evidence, render, pack, measure; recover models from bytecode, plan an entity, render the game view — no model inside.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index-vanilla", help="build a local texture index from an asset root")
    index.add_argument("--source", required=True)
    index.add_argument("--out")
    index.add_argument("--cache")
    index.add_argument("--rebuild", action="store_true")
    index.add_argument("--with-pixel-text", action="store_true")
    index.set_defaults(handler=_index_vanilla)

    listing = sub.add_parser("list-groups", help="list logical asset names (one name = all of its textures)")
    listing.add_argument("--source", action="append", metavar="PATH",
                         help="vanilla JAR, mod JAR, resource pack, or a directory with assets/; repeat to stack (later wins)")
    add_project_arguments(listing)
    listing.add_argument("--category", choices=list(CATEGORIES))
    listing.add_argument("--namespace")
    listing.add_argument("--filter")
    listing.add_argument("--min-textures", type=int, default=0)
    listing.add_argument("--limit", type=int, default=40)
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--extract", metavar="ASSET_ID", help="write every texture of one name to --to and exit")
    listing.add_argument("--to")
    listing.set_defaults(handler=_list_groups)

    ev = sub.add_parser("evidence", help="print the reference evidence a plan author needs, with no model call")
    ev.add_argument("--source", action="append", metavar="PATH")
    add_project_arguments(ev)
    ev.add_argument("--name", required=True, help="logical asset name, e.g. bow, clock, oak_log")
    ev.add_argument("--member", help="the family member this request is about, e.g. bow_standby")
    ev.add_argument("--cache")
    ev.add_argument("--max-frames", type=int, default=8)
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(handler=_evidence)

    render = sub.add_parser("render", help="rasterise, validate and package an authored plan; no model calls")
    render.add_argument("--plan", required=True)
    render.add_argument("--out", required=True)
    render.add_argument("--no-package", action="store_true")
    render.add_argument("--reference-pool", action="append", metavar="DIR",
                        help="a reference root to compare the plan against: catches a "
                             "same-class reference that was available and not attached")
    render.set_defaults(handler=_render)

    why = sub.add_parser(
        "why-reference",
        help="say which reference will paint this plan's canvas, and what the other candidates scored")
    why.add_argument("--plan", required=True)
    why.add_argument("--json", action="store_true")
    why.set_defaults(handler=_why_reference)

    refclass = sub.add_parser(
        "refclass",
        help="print the published reference class/layer table, or classify names and plans")
    refclass.add_argument("names", nargs="*", help="names to classify, e.g. deepslate iron_ore")
    refclass.add_argument("--plans", help="a directory of *.plan.json to audit by class")
    refclass.set_defaults(handler=_refclass)

    doctor = sub.add_parser(
        "doctor",
        help="check this machine and this installation: interpreters, entry points, "
             "line endings, writability, engine copies")
    doctor.set_defaults(handler=_doctor)

    gap = sub.add_parser(
        "gap-from-refs",
        help="measure the accent-to-base luma gap of reference textures, per deposit and per pixel")
    gap.add_argument("textures", nargs="*", help="reference PNGs")
    gap.add_argument("--dir", help="a reference root; every *.png in it")
    gap.add_argument("--json", action="store_true")
    gap.set_defaults(handler=_gap_from_refs)

    audit = sub.add_parser(
        "audit",
        help="measure a sprite's value bands, accent spend and family axes; gate on what you declared")
    audit.add_argument("sprites", nargs="+")
    audit.add_argument("--accent-color", action="append", metavar="HEX",
                       help="a swatch that counts as the accent; repeat for several")
    audit.add_argument("--base-color", action="append", metavar="HEX",
                       help="a swatch that counts as base material; repeat for several")
    audit.add_argument("--accent-budget", type=int,
                       help="most accent pixels allowed; 0 is a valid budget (a plain block)")
    audit.add_argument("--min-cluster", type=int, default=1,
                       help="smallest accent deposit accepted, in pixels")
    audit.add_argument("--accent-edge-max", type=float,
                       help="largest accepted accent-to-base step (p90, per channel)")
    audit.add_argument("--max-isolated", type=float,
                       help="largest accepted share of isolated opaque pixels, 0..1")
    audit.add_argument("--max-step", type=float,
                       help="largest accepted mean luma step between adjacent pixels")
    audit.add_argument("--max-hue-span", type=float, default=26.0,
                       help="family gate: widest accepted hue arc between members, degrees")
    audit.add_argument("--max-value-span", type=float, default=56.0,
                       help="family gate: widest accepted spread of member mean values")
    audit.add_argument("--max-accent-hue-span", type=float, default=14.0,
                       help="family gate: widest accepted accent-hue arc between members, degrees")
    audit.add_argument("--family", action="store_true",
                       help="also fail when the set's hue/value axes disagree")
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(handler=_audit)

    block = sub.add_parser(
        "block",
        help="emit a multi-box block model with real per-face UVs, and render it in the game's camera")
    block.add_argument("--spec", required=True,
                       help='JSON: {name, namespace, textures:{...}, elements:[{from,to,faces:{...}}]}')
    block.add_argument("--out", required=True, help="resource-pack directory to write")
    block.add_argument("--render", metavar="PNG", help="also render the model to this image")
    block.add_argument("--view", action="append", metavar="NAME",
                       help="front34 / front / side / back34 / top34; repeat for a strip")
    block.add_argument("--pack-format", type=int, default=15)
    block.set_defaults(handler=_block)

    layouts = sub.add_parser("layouts", help="list the shipped entity UV layouts so one can be found, not invented")
    layouts.add_argument("--root", help="layouts directory; defaults to the one shipped with this skill")
    layouts.add_argument("--json", action="store_true")
    layouts.set_defaults(handler=_layouts)

    boxes = sub.add_parser("boxes", help="derive a UV layout from a box decomposition you wrote")
    boxes.add_argument("--spec", required=True, help="JSON: {boxes:[{id,part_id,size:[w,h,d],origin:[x,y,z]}]}")
    boxes.add_argument("--out", required=True, help="where to write the layout JSON")
    boxes.add_argument("--canvas", type=int, help="force an atlas width; default grows to fit")
    boxes.add_argument("--margin", type=int, default=0, help="blank rows between shelves")
    boxes.set_defaults(handler=_boxes)

    uv = sub.add_parser("uv", help="render an entity atlas against its layout: annotated map + previews")
    uv.add_argument("--layout", required=True)
    uv.add_argument("--texture", required=True)
    uv.add_argument("--out", required=True)
    uv.add_argument("--scale", type=int, default=8)
    uv.set_defaults(handler=_uv)

    pack = sub.add_parser("pack", help="assemble several textures into one face-correct resource pack")
    pack.add_argument("--manifest", required=True, help="JSON naming the textures and declaring each block model")
    pack.add_argument("--out", required=True)
    pack.set_defaults(handler=_pack)

    measure = sub.add_parser("measure", help="frame-to-frame agreement for a set of sprites")
    measure.add_argument("sprites", nargs="+")
    measure.add_argument("--baseline", action="append", default=[])
    measure.set_defaults(handler=_measure)
    model = sub.add_parser(
        "model",
        help="read an entity model's boxes out of the jar's own bytecode and emit a layout")
    model.add_argument("--jar", help="vanilla, Forge or mod jar holding the model classes")
    model.add_argument("--java", metavar="FILE", help="read a Java model class instead, for a mod you are writing")
    model.add_argument("--texture", help="texture path inside the jar, e.g. textures/entity/sheep/sheep.png")
    model.add_argument("--model-class", help="read this obfuscated model class directly")
    model.add_argument("--out", help="write a layout document here")
    model.add_argument("--list", action="store_true",
                       help="list every texture in the archive that a model draws")
    model.add_argument("--all", action="store_true",
                       help="emit a render spec for every such texture into --out")
    model.add_argument("--list-models", action="store_true",
                       help="list every class in the archive that builds a model (works when paths are built at runtime)")
    model.add_argument("--all-models", action="store_true",
                       help="emit a spec for every such model class into --out")
    model.add_argument("--filter", help="only names containing this")
    model.add_argument("--emit-spec", metavar="FILE",
                       help="write the full render spec (parts, pivots, boxes, pose) for mc-art ingame")
    model.add_argument("--texture-width", type=int)
    model.add_argument("--texture-height", type=int)
    model.set_defaults(handler=_model)

    ingame = sub.add_parser(
        "ingame",
        help="render the game's own view of a model, so a delivery is looked at before it ships")
    ingame.add_argument("--selftest", action="store_true",
                        help="assert the quad corner order and write the glyph control")
    ingame.add_argument("--control", action="append", metavar="NAME",
                        help="cow / sheep / wool / both / slime / all -- render a known-good control first")
    ingame.add_argument("--block", metavar="NAME", help="a block model under models/block/")
    ingame.add_argument("--model", metavar="SPEC.json", help="an entity model spec")
    ingame.add_argument("--source", metavar="PATH", help="game jar, mod jar, or asset directory")
    ingame.add_argument("--texture", metavar="PATH", help="texture for --model")
    ingame.add_argument("--out", required=True, help="output image, or directory for --selftest/--control")
    ingame.add_argument("--view", action="append", metavar="NAME", help="front34 / front / side / back34 / top34")
    ingame.add_argument("--width", type=int, default=800)
    ingame.add_argument("--height", type=int, default=560)
    ingame.add_argument("--from", dest="frm", metavar="X,Y,Z", help="block camera position")
    ingame.add_argument("--at", metavar="X,Y,Z", help="block camera target")
    ingame.set_defaults(handler=_ingame)

    entity = sub.add_parser(
        "entity",
        help="plan an entity from one model description: emit the layout to paint and the spec to render")
    entity.add_argument("--spec", required=True, metavar="MODEL.json",
                        help="parts with pivots and boxes; u/v optional, assigned by the packer when absent")
    entity.add_argument("--out", required=True, help="directory for layout.json and model.json")
    entity.add_argument("--atlas", metavar="PNG", help="also check a painted atlas against the boxes")
    entity.add_argument("--canvas-width", type=int, help="packing width; defaults to 64 or the widest net")
    entity.add_argument("--java", metavar="FILE_OR_DIR",
                        help="also write the mod's model class, so the code and the atlas cannot drift")
    entity.add_argument("--class-name", help="class to generate; defaults to Model<Name>")
    entity.add_argument("--package", help="package for the generated class")
    entity.set_defaults(handler=_entity)

    return parser


def _pin_utf8_stdio():
    """把 stdout/stderr 钉成 UTF-8。

    为什么要：Windows 上 Python 的 stdio 用**系统区域编码**（中文机器上是 GBK），
    于是这里 print 出去的中文（资产名、命名空间、日志行）到别的程序眼里就是 `����ʯ`
    —— 而 DSH 的 shell 与面板都按 UTF-8 解。`-X utf8` 会被环境里的 `PYTHONIOENCODING`
    盖过，`reconfigure` 谁也盖不过，所以在这里钉死。旧 Python 没有 reconfigure，包在 try 里。
    """
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _pin_utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:  # noqa: BLE001
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
