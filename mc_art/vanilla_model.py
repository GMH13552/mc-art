"""Recover an entity model's boxes from the compiled game code.

A 1.12 asset root has no entity model files at all -- `assets/minecraft/models/`
holds 878 block and 717 item JSONs and zero entity ones. A mob's geometry and
its texture offsets live only in Java (`ModelSheep1`, `ModelBiped`), whose sole
on-disk record is bytecode, usually obfuscated: the class that draws
`textures/entity/sheep/sheep.png` is called `cao`, and the model it builds is
`bqp`. So a layout for a mod's or vanilla's entity texture has to be *read*,
not remembered -- this module reads it.

    src = JarSource("client.jar")
    for box in boxes_for_texture(src, "textures/entity/sheep/sheep.png"):
        print(box.part, box.u, box.v, box.w, box.h, box.d)

The texture itself stays the authority: `verify_texture` reports how much of
the artwork the recovered boxes actually claim.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

__all__ = [
    "Box",
    "JarSource",
    "UnsupportedBytecode",
    "boxes_for_class",
    "boxes_for_texture",
    "layout_document",
    "models_for_class",
    "models_for_texture",
    "parse_constructor",
    "resolve_model",
]

_OP_RE = re.compile(r"^\s+(\d+):\s+(\S+)(?:\s+(.*))?$")
_BOXFACE_SIGS = ("(FFFIIIF)V", "(FFFIII)Lbrs;", "(FFFIII)V")
_ROT_SIG = "(FFF)V"
_RENDERER_SIG = "(Lbqf;II)V"
_OFFSET_SIGS = ("(II)Lbrs;", "(II)V")
_SUPER_SIG = "(IF)V"
_ARITH = ("iadd", "isub", "imul", "idiv", "fadd", "fsub", "fmul", "fdiv")


class UnsupportedBytecode(RuntimeError):
    """The constructor used a shape this reader does not model."""


class _Unknown:
    __slots__ = ("tag",)

    def __init__(self, tag: str):
        self.tag = tag

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<%s>" % self.tag


@dataclass(frozen=True)
class Box:
    """One `ModelRenderer.addBox` call, in texture and model space."""

    part: str
    u: int
    v: int
    w: int
    h: int
    d: int
    offset: tuple[float, float, float]
    delta: float = 0.0
    rotation: tuple[float, float, float] | None = None

    @property
    def net_size(self) -> tuple[int, int]:
        """The atlas rectangle this box occupies: (width, height)."""
        return (2 * self.d + 2 * self.w, self.d + self.h)

    def faces(self) -> dict[str, list[int]]:
        """The six half-open [left, top, right, bottom] rectangles of the net."""
        u, v, w, h, d = self.u, self.v, self.w, self.h, self.d
        return {
            "top": [u + d, v, u + d + w, v + d],
            "bottom": [u + d + w, v, u + d + 2 * w, v + d],
            "left": [u, v + d, u + d, v + d + h],
            "front": [u + d, v + d, u + d + w, v + d + h],
            "right": [u + d + w, v + d, u + 2 * d + w, v + d + h],
            "back": [u + 2 * d + w, v + d, u + 2 * d + 2 * w, v + d + h],
        }

    def occupied(self) -> Iterable[tuple[int, int]]:
        for left, top, right, bottom in self.faces().values():
            for y in range(top, bottom):
                for x in range(left, right):
                    yield (x, y)


# --------------------------------------------------------------------------
# bytecode reading
# --------------------------------------------------------------------------


def _methods(text: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    cur: str | None = None
    atoms: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _OP_RE.match(line)
        if match:
            if cur:
                atoms.append((match.group(2), (match.group(3) or "").strip()))
            continue
        stripped = line.rstrip()
        if stripped.startswith("  ") and not stripped.startswith("      ") \
                and "(" in stripped and stripped.endswith(";"):
            if cur:
                out[cur] = atoms
            cur = stripped.strip().split("(")[0].split()[-1]
            atoms = []
    if cur:
        out[cur] = atoms
    return out


def _decode(op: str, raw: str) -> tuple | None:
    body = raw.split("//", 1)[1].strip() if "//" in raw else raw.strip()
    if op == "new" and body.startswith("class "):
        return ("new", body[6:])
    if op == "getfield" and body.startswith("Field "):
        return ("fieldref", body[6:].split(":")[0])
    if op in ("invokevirtual", "invokespecial", "invokestatic", "invokeinterface"):
        match = re.match(r"Method (\S+?)\.(\S+?):(\S+)$", body)
        if match:
            return ("call", match.group(1), match.group(2), match.group(3))
        return None
    if op == "iconst_m1":
        return ("const", -1)
    if op.startswith("iconst_"):
        return ("const", int(op.split("_")[1]))
    if op.startswith("fconst_"):
        return ("const", float(op.split("_")[1]))
    if op in ("bipush", "sipush"):
        return ("const", int(body))
    if op == "ldc":
        for prefix, cast in (("float ", lambda s: float(s.rstrip("f"))), ("int ", int), ("String ", str)):
            if body.startswith(prefix):
                return ("const", cast(body[len(prefix):]))
        return None
    if op == "aload_0":
        return ("arg", 0)
    for kind in ("load", "store"):
        match = re.match(r"^(?:i|f|l|d|a)%s(_\d+)?$" % kind, op)
        if match:
            index = int(match.group(1)[1:]) if match.group(1) else (int(body) if body.isdigit() else None)
            return (kind, index)
    return ("op", op)


def _arith(a: Any, b: Any, op: str) -> Any:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        try:
            return {"iadd": a + b, "isub": a - b, "imul": a * b, "idiv": a // b,
                    "fadd": a + b, "fsub": a - b, "fmul": a * b, "fdiv": a / b}[op]
        except (KeyError, ZeroDivisionError):
            return _Unknown(op)
    return _Unknown(op)


def _is_ref(value: Any) -> bool:
    return isinstance(value, tuple) and len(value) == 2 and value[0] == "ref" and value[1] is not None


def parse_constructor(text: str, cls: str, args: list[Any] | None = None):
    """Interpret one constructor.

    Returns `(records, (superclass, superargs) | None)`. A record is a
    ModelRenderer: the field it is assigned to, every box added to it (a renderer
    can hold several -- ModelCow attaches two horns with `setTextureOffset`),
    and its rotation point.
    """
    ops = _methods(text).get(cls) or []
    recs: list[dict[str, Any]] = []
    field_of: dict[str, int] = {}
    stack: list[Any] = []
    locals_: dict[int, Any] = {i + 1: a for i, a in enumerate(args or [])}
    supercall = None

    def pop():
        return stack.pop() if stack else _Unknown("empty")

    for op, raw in ops:
        if op == "putfield":
            body = raw.split("//", 1)[1].strip() if "//" in raw else raw
            name = body[6:].split(":")[0] if body.startswith("Field ") else None
            value = pop()
            pop()
            if name and _is_ref(value):
                field_of[name] = value[1]
                recs[value[1]]["field"] = name
            continue
        decoded = _decode(op, raw)
        if decoded is None:
            if op in ("pop", "pop2"):
                pop()
            continue
        kind = decoded[0]
        if kind == "const":
            stack.append(decoded[1])
        elif kind in ("arg", "load"):
            stack.append(("self",) if decoded[1] == 0
                         else locals_.get(decoded[1], _Unknown("loc%s" % decoded[1])))
        elif kind == "store":
            locals_[decoded[1]] = pop()
        elif kind == "new":
            stack.append(("new", decoded[1]))
        elif kind == "fieldref":
            stack.append(("ref", field_of.get(decoded[1])))
        elif kind == "op":
            if op in _ARITH:
                b, a = pop(), pop()
                stack.append(_arith(a, b, op))
            elif op in ("i2f", "f2i", "i2d", "d2i"):
                stack.append(_Unknown("cast"))
            elif op == "dup":
                stack.append(stack[-1] if stack else _Unknown("dup"))
        elif kind == "call":
            owner, _name, sig = decoded[1], decoded[2], decoded[3]
            if sig == _RENDERER_SIG:
                v, u = pop(), pop()
                pop()
                pop()
                recs.append({"uv": (u, v), "boxes": [], "rot": None, "field": None})
                stack.append(("ref", len(recs) - 1))
            elif sig in _OFFSET_SIGS:
                v, u = pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    recs[ref[1]]["uv"] = (u, v)
                if sig.endswith("Lbrs;"):
                    stack.append(ref)
            elif sig in _BOXFACE_SIGS:
                delta = pop() if sig == "(FFFIIIF)V" else 0.0
                d, h, w = pop(), pop(), pop()
                z, y, x = pop(), pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    u, v = recs[ref[1]]["uv"]
                    recs[ref[1]]["boxes"].append((u, v, x, y, z, w, h, d, delta))
                if sig.endswith("Lbrs;"):
                    stack.append(ref)
            elif sig == _ROT_SIG:
                z, y, x = pop(), pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    recs[ref[1]]["rot"] = (x, y, z)
            elif sig == _SUPER_SIG:
                f, i = pop(), pop()
                supercall = (owner, [i, f])
    return recs, supercall


def _new_classes(text: str, cls: str) -> list[str]:
    out: list[str] = []
    for op, raw in (_methods(text).get(cls) or []):
        decoded = _decode(op, raw)
        if decoded and decoded[0] == "new" and decoded[1] not in out:
            out.append(decoded[1])
    return out


def resolve_model(source: Callable[[str], str], cls: str, args: list[Any] | None = None,
                  depth: int = 0) -> dict[str, dict[str, Any]]:
    """Every ModelRenderer part of a model, walking the super constructor.

    `ModelQuadruped(12, 0.0F)` owns the four legs; a subclass that only
    overrides head and body inherits them, with the arguments threaded through
    so `4 x par1 x 4` resolves to a real number.
    """
    text = source(cls)
    recs, supercall = parse_constructor(text, cls, args)
    parts: dict[str, dict[str, Any]] = {}
    if supercall and supercall[0] not in ("bqf",) and depth < 6:
        parts = resolve_model(source, supercall[0], supercall[1], depth + 1)
    for rec in recs:
        name = rec["field"]
        if not name:
            continue
        merged = dict(parts.get(name, {}))
        for key in ("uv", "boxes", "rot"):
            if rec.get(key) is not None:
                merged[key] = rec[key]
        parts[name] = merged
    return parts


def models_for_class(source: Callable[[str], str], cls: str, depth: int = 2) -> list[str]:
    """A renderer builds its model; a layer class builds a renderer that does.

    Search breadth-first for the nearest classes that actually declare boxes, so
    RenderSheep resolves to ModelSheep1 and its wool layer to ModelSheep2.
    """
    level, seen = [cls], {cls}
    for _ in range(depth + 1):
        found: list[str] = []
        nxt: list[str] = []
        for candidate in level:
            recs, _ = parse_constructor(source(candidate), candidate)
            if recs:
                found.append(candidate)
            else:
                for new_class in _new_classes(source(candidate), candidate):
                    if new_class not in seen:
                        seen.add(new_class)
                        nxt.append(new_class)
        if found:
            return found
        level = nxt
    return []


def boxes_for_class(source: Callable[[str], str], cls: str) -> list[Box]:
    out: list[Box] = []
    for name, part in sorted(resolve_model(source, cls).items()):
        for u, v, x, y, z, w, h, d, delta in part.get("boxes", []):
            if not all(isinstance(value, int) for value in (u, v, w, h, d)):
                continue
            out.append(Box(part=name, u=u, v=v, w=w, h=h, d=d,
                           offset=(x, y, z), delta=float(delta) if isinstance(delta, (int, float)) else 0.0,
                           rotation=tuple(part["rot"]) if part.get("rot") else None))
    return out


# --------------------------------------------------------------------------
# jar access
# --------------------------------------------------------------------------


class JarSource:
    """javap-backed class text for one jar.

    Only the few classes a query touches are ever written to disk; the constant
    pool scan that finds them reads the archive in memory.
    """

    def __init__(self, jar: str | Path, keep_temp: bool = False):
        self.jar = Path(jar)
        self._zip = zipfile.ZipFile(self.jar)
        self._tmp = Path(tempfile.mkdtemp(prefix="mc-art-model-"))
        self._keep = keep_temp
        self._written: set[str] = set()
        self._text: dict[str, str] = {}
        self._strings: list[tuple[str, bytes]] | None = None
        if shutil.which("javap") is None:
            raise UnsupportedBytecode(
                "javap is not on PATH; it ships with any JDK and is what turns the "
                "obfuscated model classes back into readable constructors")

    # -- archive ---------------------------------------------------------
    def _class_bytes(self, name: str) -> bytes | None:
        for candidate in (name + ".class",):
            try:
                return self._zip.read(candidate)
            except KeyError:
                continue
        return None

    def _scan(self) -> list[tuple[str, bytes]]:
        if self._strings is None:
            found = []
            for entry in self._zip.namelist():
                if not entry.endswith(".class") or "/" in entry:
                    continue
                found.append((entry[:-6], self._zip.read(entry)))
            self._strings = found
        return self._strings

    def texture_owners(self, relative: str) -> list[str]:
        """Every class whose constant pool mentions this texture path."""
        needle = relative.encode()
        return [name for name, blob in self._scan() if needle in blob]

    def texture_bytes(self, relative: str) -> bytes | None:
        """The raw bytes of a texture path, whichever namespace holds it."""
        for entry in self._zip.namelist():
            if entry.endswith("/" + relative):
                return self._zip.read(entry)
        return None

    # -- class text ------------------------------------------------------
    def text(self, cls: str) -> str:
        if cls in self._text:
            return self._text[cls]
        blob = self._class_bytes(cls)
        if blob is None:
            raise KeyError("class %s is not in %s" % (cls, self.jar))
        if cls not in self._written:
            (self._tmp / (cls + ".class")).write_bytes(blob)
            self._written.add(cls)
        result = subprocess.run(
            ["javap", "-p", "-c", "-cp", str(self._tmp), cls],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise UnsupportedBytecode(result.stderr.strip() or ("javap failed on %s" % cls))
        self._text[cls] = result.stdout
        return result.stdout

    def close(self) -> None:
        self._zip.close()
        if not self._keep:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> "JarSource":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


def models_for_texture(source: JarSource, relative: str) -> list[str]:
    """The nearest model class(es) that draw a texture path."""
    found: list[str] = []
    for owner in source.texture_owners(relative):
        for model in models_for_class(source.text, owner):
            if model not in found:
                found.append(model)
    return found


def boxes_for_texture(source: JarSource, relative: str) -> list[Box]:
    out: list[Box] = []
    for model in models_for_texture(source, relative):
        out.extend(boxes_for_class(source.text, model))
    return out


def verify_texture(boxes: list[Box], image) -> dict[str, Any]:
    """How much of the artwork the recovered boxes claim.

    `outside` is the number that matters: painted pixels no box covers at all
    mean a part is missing. `transparent_inside` is normally non-zero because
    vanilla leaves never-visible faces blank.
    """
    width, height = image.size
    pixels = image.convert("RGBA").load()
    painted = {(x, y) for y in range(height) for x in range(width) if pixels[x, y][3] > 0}
    claimed: set[tuple[int, int]] = set()
    for box in boxes:
        claimed.update(box.occupied())
    return {
        "painted": len(painted),
        "claimed": len(claimed),
        "outside": len(painted - claimed),
        "transparent_inside": len(claimed - painted),
        "covered_fraction": (len(painted & claimed) / len(painted)) if painted else 0.0,
    }


def layout_document(boxes: list[Box], *, name: str, source: str, texture_width: int,
                    texture_height: int, notes: str = "", part_prefix: str = "") -> dict[str, Any]:
    """Render recovered boxes as a shipped-layout document.

    Preview placements are an orthographic front projection of the real model
    coordinates, so a part that sits in front of another draws over it.
    """
    placements: list[tuple[float, float, int, int, int, str]] = []
    for box in boxes:
        rx, ry, rz = box.rotation or (0.0, 0.0, 0.0)
        placements.append((rx + box.offset[0], ry + box.offset[1],
                           int(round(rz)), box.w, box.h, box.part))
    if placements:
        min_x = min(p[0] for p in placements)
        min_y = min(p[1] for p in placements)
    else:  # pragma: no cover - a model with no boxes has no layout
        min_x = min_y = 0.0
    margin = 2
    cubes = []
    for box in boxes:
        rx, ry, rz = box.rotation or (0.0, 0.0, 0.0)
        origin = [int(round(rx + box.offset[0] - min_x)) + margin,
                  int(round(ry + box.offset[1] - min_y)) + margin]
        depth_layer = int(round(64 - rz))
        part = (part_prefix + box.part) if box.part else box.part
        cubes.append({
            "id": "%s_%d_%d" % (part, box.u, box.v),
            "part_id": part,
            "u": box.u, "v": box.v,
            "width": box.w, "height": box.h, "depth": box.d,
            "preview_instances": [[origin[0], origin[1], box.w, box.h]],
            "preview_layer": depth_layer,
            "notes": "addBox(%g,%g,%g,%d,%d,%d%s)%s" % (
                box.offset[0], box.offset[1], box.offset[2], box.w, box.h, box.d,
                (", %g" % box.delta) if box.delta else "",
                (" rot=(%g,%g,%g)" % box.rotation) if box.rotation else ""),
        })
    return {
        "format": "minecraft_1_12_modelrenderer_cube_uv",
        "source": source,
        "texture_width": texture_width,
        "texture_height": texture_height,
        "notes": notes or (
            "Recovered from the model's own bytecode by mc_art.vanilla_model; "
            "preview placements are a front projection of the model coordinates."),
        "cubes": cubes,
    }
