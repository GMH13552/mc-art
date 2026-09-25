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


def _model(args: argparse.Namespace) -> int:
    """Read an entity model out of compiled game code instead of guessing it."""
    from .vanilla_model import (JarSource, boxes_for_class, boxes_for_texture,
                                layout_document, models_for_texture)

    with JarSource(args.jar) as source:
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
            from .ingame import describe

            print("MODEL   %s" % describe(spec))
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
        description="Deterministic Minecraft art engine. Scan, evidence, render, pack, measure, recover models from bytecode, render the game view — no model inside.",
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
    model.add_argument("--jar", required=True, help="vanilla, Forge or mod jar holding the model classes")
    model.add_argument("--texture", help="texture path inside the jar, e.g. textures/entity/sheep/sheep.png")
    model.add_argument("--model-class", help="read this obfuscated model class directly")
    model.add_argument("--out", help="write a layout document here")
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
