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
from pathlib import Path

from .asset_groups import CATEGORIES, build_catalogue
from .contracts import ReferenceAsset, ReferenceRole
from .evidence import _appearance_reference_evidence, shape_authority
from .group_index import GroupReferenceSource
from .planfile import plan_from_file
from .reference_index import build_index


def _default_cache() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / ".cache" / "live"


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
    catalogue = build_catalogue(args.source)
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


def _evidence(args: argparse.Namespace) -> int:
    """Everything a plan author needs about one asset, with no model call.

    The reference list, the role each carries, which member answers this
    request, and the literal pixel text of every small raster -- so the caller
    can write the descriptor, geometry and appearance itself.
    """
    catalogue = build_catalogue(args.source)
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

    plan = plan_from_file(args.plan)
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
    return 0 if result.sprite_path and result.validation.passed else 1


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc_art",
        description="Deterministic Minecraft art engine: scan, evidence, render, measure.",
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
    listing.add_argument("--source", action="append", required=True, metavar="PATH",
                         help="vanilla JAR, mod JAR, resource pack, or a directory with assets/; repeat to stack (later wins)")
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
    ev.add_argument("--source", action="append", required=True, metavar="PATH")
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
    render.set_defaults(handler=_render)

    pack = sub.add_parser("pack", help="assemble several textures into one face-correct resource pack")
    pack.add_argument("--manifest", required=True, help="JSON naming the textures and declaring each block model")
    pack.add_argument("--out", required=True)
    pack.set_defaults(handler=_pack)

    measure = sub.add_parser("measure", help="frame-to-frame agreement for a set of sprites")
    measure.add_argument("sprites", nargs="+")
    measure.add_argument("--baseline", action="append", default=[])
    measure.set_defaults(handler=_measure)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except Exception as exc:  # noqa: BLE001
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
