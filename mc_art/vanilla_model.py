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

import math
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
    "method_blocks",
    "parse_constructor",
    "pose_constants",
    "render_spec",
    "renderer_of",
    "resolve_model",
    "transform_fields",
]

_OP_RE = re.compile(r"^\s+(\d+):\s+(\S+)(?:\s+(.*))?$")
# Signatures are matched by shape, not by the obfuscated class they belong to:
# the names in a jar depend on the mapping it was built against, and a mod jar
# brings its own. ModelRenderer.addBox is (FFFIIIF)V or (FFFIII)L<self>;,
# setTextureOffset is (II)L<self>;, setRotationPoint is (FFF)V, the renderer
# constructor is (L<ModelBase>;II)V and a model's super call is (IF)V.
_RENDERER_RE = re.compile(r"^\(L[^;]+;II\)V$")
_BOXFACE_RES = (re.compile(r"^\(FFFIIIF\)V$"), re.compile(r"^\(FFFIII\)L[^;]+;$"))
_ROT_RE = re.compile(r"^\(FFF\)V$")
_OFFSET_RE = re.compile(r"^\(II\)L[^;]+;$")
_SUPER_RE = re.compile(r"^\(IF\)V$")
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
            cur = stripped.strip().split("(")[0].split()[-1].rsplit(".", 1)[-1]
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
    simple = cls.replace("/", ".").rsplit(".", 1)[-1]
    ops = _methods(text).get(simple) or _methods(text).get(cls) or []
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
            elif op in ("i2f", "i2d", "i2l", "f2i", "f2d", "f2l",
                        "d2i", "d2f", "d2l", "l2i", "l2f", "l2d"):
                # a numeric conversion consumes its operand; forgetting the pop
                # shifts the whole stack by one and the receiver for the next
                # call is read off the wrong slot
                value = pop()
                stack.append(float(value) if isinstance(value, (int, float))
                             else _Unknown("cast"))
            elif op == "dup":
                stack.append(stack[-1] if stack else _Unknown("dup"))
            elif op == "dup_x1":
                if len(stack) >= 2:
                    first = stack.pop()
                    second = stack.pop()
                    stack.extend((first, second, first))
            elif op == "dup_x2":
                if len(stack) >= 3:
                    first = stack.pop()
                    second = stack.pop()
                    third = stack.pop()
                    stack.extend((first, third, second, first))
            elif op == "dup2":
                if len(stack) >= 2:
                    top = stack.pop()
                    below = stack.pop()
                    stack.extend((below, top, below, top))
        elif kind == "call":
            owner, _name, sig = decoded[1], decoded[2], decoded[3]
            if _RENDERER_RE.match(sig):
                v, u = pop(), pop()
                pop()
                pop()
                recs.append({"uv": (u, v), "boxes": [], "rot": None, "field": None})
                stack.append(("ref", len(recs) - 1))
            elif _OFFSET_RE.match(sig):
                v, u = pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    recs[ref[1]]["uv"] = (u, v)
                if sig.endswith(";"):
                    stack.append(ref)
            elif any(pattern.match(sig) for pattern in _BOXFACE_RES):
                delta = pop() if sig.endswith("IIIF)V") else 0.0
                d, h, w = pop(), pop(), pop()
                z, y, x = pop(), pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    u, v = recs[ref[1]]["uv"]
                    recs[ref[1]]["boxes"].append((u, v, x, y, z, w, h, d, delta))
                if sig.endswith(";"):
                    stack.append(ref)
            elif _ROT_RE.match(sig):
                z, y, x = pop(), pop(), pop()
                ref = pop()
                if _is_ref(ref):
                    recs[ref[1]]["rot"] = (x, y, z)
            elif _SUPER_RE.match(sig):
                f, i = pop(), pop()
                supercall = (owner, [i, f])
    return recs, supercall


def _new_classes(text: str, cls: str) -> list[str]:
    simple = cls.replace("/", ".").rsplit(".", 1)[-1]
    out: list[str] = []
    for op, raw in (_methods(text).get(simple) or _methods(text).get(cls) or []):
        decoded = _decode(op, raw)
        if decoded and decoded[0] == "new" and decoded[1] not in out:
            out.append(decoded[1])
    return out


# --------------------------------------------------------------------------
# the pose, which lives in setRotationAngles rather than the constructor
# --------------------------------------------------------------------------

_FIELD_RE = re.compile(r"^\s+(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?"
                       r"([\w.$\[\]]+)\s+(\w+);\s*$")


def method_blocks(text):
    """[(name, arg list as printed, atoms)] -- keeping the arg list, because an
    obfuscated class overloads one name for several methods."""
    out, cur = [], None
    for line in text.splitlines():
        match = _OP_RE.match(line)
        if match:
            if cur is not None:
                cur[2].append((match.group(2), (match.group(3) or "").strip()))
            continue
        stripped = line.rstrip()
        if stripped.startswith("  ") and not stripped.startswith("      ") \
                and "(" in stripped and stripped.endswith(");"):
            head = stripped.strip()
            name = head.split("(")[0].split()[-1].rsplit(".", 1)[-1]
            cur = [name, head[head.index("(") + 1:head.rindex(")")], []]
            out.append(cur)
    return out


def declared_fields(text):
    """[(type, name)] in declaration order."""
    out = []
    for line in text.splitlines():
        match = _FIELD_RE.match(line)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def _fields_written(atoms):
    out = []
    for op, raw in atoms:
        if op != "putfield":
            continue
        body = raw.split("//", 1)[1].strip() if "//" in raw else raw
        match = re.match(r"Field (?:[\w/$]+\.)?(\w+):", body)
        if match and match.group(1) not in out:
            out.append(match.group(1))
    return out


def transform_fields(renderer_text):
    """(rotation point fields, rotation angle fields) for one ModelRenderer.

    setRotationPoint is the method that writes exactly three floats, and in the
    renderer's own field order the three angle fields sit immediately after the
    three point fields -- so this is derived, never a hard-coded a/b/c.
    """
    floats = [name for kind, name in declared_fields(renderer_text) if kind == "float"]
    point = None
    for _name, args, atoms in method_blocks(renderer_text):
        if args.strip() != "float, float, float":
            continue
        written = _fields_written(atoms)
        if len(written) == 3:
            point = written
            break
    if point is None or point[-1] not in floats:
        return None, None
    index = floats.index(point[-1])
    angles = floats[index + 1:index + 4]
    return (point, angles if len(angles) == 3 else None)


def _constant_value(atom):
    op, raw = atom
    body = raw.split("//", 1)[1].strip() if "//" in raw else raw.strip()
    if op == "ldc" and body.startswith("float "):
        return float(body[6:].rstrip("f"))
    if op.startswith("fconst_"):
        return float(op.split("_")[1])
    if op == "iconst_m1":
        return -1.0
    if op.startswith("iconst_"):
        return float(op.split("_")[1])
    if op in ("bipush", "sipush") and body.lstrip("-").isdigit():
        return float(body)
    return None


def _receiver_field(atoms, index):
    """The model field a putfield assigns into, for an assignment of the shape
    aload_0 / getfield X / const / putfield renderer.angle."""
    for step in range(2, 5):
        position = index - step
        if position < 0:
            return None
        op, raw = atoms[position]
        if op == "aload_0":
            return None
        if op == "getfield":
            body = raw.split("//", 1)[1].strip() if "//" in raw else raw
            match = re.match(r"Field (\w+):", body)
            if match:
                return match.group(1)
    return None


def super_of(source: Callable[[str], str], cls: str):
    """The class this one extends, as javap prints it."""
    try:
        text = source(cls)
    except KeyError:
        return None
    for line in text.splitlines()[:4]:
        if " extends " in line:
            name = line.split(" extends ")[1].split()[0].strip("{").strip()
            return None if name in ("java.lang.Object", "Object") else name
    return None


def pose_constants(source: Callable[[str], str], cls: str, renderer: str, angles, depth=0):
    """Rotation angles the model's setRotationAngles assigns a bare constant to.

    A per-frame term (headPitch times 0.017) never lands here: the write is
    preceded by arithmetic rather than by the constant, so it is skipped and that
    part keeps the neutral pose. What is left is the posing the model applies
    unconditionally -- a quadruped's torso is the one that matters.
    """
    if not angles or depth > 5:
        return {}
    axis_of = dict(zip(angles, ("x", "y", "z")))
    owners = (renderer, renderer.replace(".", "/"))
    # a pose is usually set by a base class and merely extended by the concrete
    # one (ModelQuadruped poses the torso, ModelSheep1 only adds head tracking),
    # so walk up and let the most derived assignment win per axis
    out: dict[str, dict[str, float]] = {}
    parent = super_of(source, cls)
    if parent and depth < 5:
        out = pose_constants(source, parent, renderer, angles, depth + 1)
    try:
        blocks = method_blocks(source(cls))
    except KeyError:
        # a partial jar, or a fixture set that stops at the model base class
        return out
    for _name, args, atoms in blocks:
        if not args.strip().startswith("float, float, float, float, float, float"):
            continue
        for index, (op, raw) in enumerate(atoms):
            if op != "putfield" or index == 0:
                continue
            body = raw.split("//", 1)[1].strip() if "//" in raw else raw
            match = re.match(r"Field ([\w/$]+)\.(\w+):F$", body)
            if not match or match.group(1) not in owners:
                continue
            axis = axis_of.get(match.group(2))
            if axis is None:
                continue
            value = _constant_value(atoms[index - 1])
            if value is None:
                continue
            target = _receiver_field(atoms, index)
            if target:
                out.setdefault(target, {})[axis] = value
    return out


# a ModelRenderer's own constructor is the (ModelBase, int, int) one; a nested
# model class would instead be seen calling it, so accept either
_RENDERER_CTOR_ARGS = re.compile(r"^[\w.$]+, int, int$")


def renderer_of(source: Callable[[str], str], cls: str):
    """The ModelRenderer class a model constructs."""
    for candidate in _new_classes(source(cls), cls):
        simple = candidate.replace("/", ".").rsplit(".", 1)[-1]
        for name, args, atoms in method_blocks(source(candidate)):
            if name == simple and _RENDERER_CTOR_ARGS.match(args.strip()):
                return candidate
            for op, raw in atoms:
                decoded = _decode(op, raw)
                if decoded and decoded[0] == "call" and _RENDERER_RE.match(decoded[3]):
                    return candidate
    return None


def render_spec(source: Callable[[str], str], cls: str, *, texture=None, tex_size=(64, 32)):
    """A model as the in-game renderer wants it: parts, pivots, boxes and pose.

    This is the bridge from bytecode to mc-art ingame, and it exists so that a
    render spec is never hand-typed: the hand-typed one lost two of a sheep's
    legs and still produced a plausible picture.
    """
    renderer = renderer_of(source, cls)
    angles = None
    if renderer:
        _points, angles = transform_fields(source(renderer))
    pose = pose_constants(source, cls, renderer, angles) if renderer else {}
    parts = []
    for field, part in sorted(resolve_model(source, cls).items()):
        boxes = []
        for u, v, x, y, z, w, h, d, delta in part.get("boxes", []):
            if not all(isinstance(value, int) for value in (u, v, w, h, d)):
                continue
            boxes.append({"u": u, "v": v, "at": [x, y, z], "w": w, "h": h, "d": d,
                          "inflate": float(delta) if isinstance(delta, (int, float)) else 0.0})
        if boxes:
            # the bytecode holds radians (Math.PI / 2 is 1.5707964f) and the
            # renderer speaks degrees, which is a silent, plausible-looking way
            # to get a sheep whose torso stands on end. Round hard: a float32
            # pi/2 is 1.5707964, whose degrees are 90.000004, and the model
            # meant 90.
            rotation = {axis: round(math.degrees(value), 4)
                        for axis, value in pose.get(field, {}).items()}
            parts.append({"name": field,
                          "pivot": [float(value) for value in (part.get("rot") or (0.0, 0.0, 0.0))],
                          "rot": rotation,
                          "boxes": boxes})
    return {"tex": list(tex_size), "texture": texture, "parts": parts,
            "units": {"rot": "degrees",
                      "space": "model space, y down, z backward, 1 unit = 1/16 block"},
            "source_class": cls, "renderer_class": renderer, "angle_fields": angles}


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
    if supercall and depth < 6:
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
        entry = self._normalise(name).replace(".", "/") + ".class"
        try:
            return self._zip.read(entry)
        except KeyError:
            return None

    def _scan(self) -> list[tuple[str, bytes]]:
        if self._strings is None:
            found = []
            for entry in self._zip.namelist():
                if entry.endswith(".class"):
                    found.append((entry[:-6], self._zip.read(entry)))
            self._strings = found
        return self._strings

    @staticmethod
    def _normalise(cls: str) -> str:
        return cls.replace("/", ".").strip(".")

    def texture_owners(self, relative: str) -> list[str]:
        """Every class whose constant pool mentions this texture path."""
        needle = relative.encode()
        return [self._normalise(name) for name, blob in self._scan() if needle in blob]

    def texture_bytes(self, relative: str) -> bytes | None:
        """The raw bytes of a texture path, whichever namespace holds it."""
        for entry in self._zip.namelist():
            if entry.endswith("/" + relative):
                return self._zip.read(entry)
        return None

    # -- class text ------------------------------------------------------
    def text(self, cls: str) -> str:
        name = self._normalise(cls)
        if name in self._text:
            return self._text[name]
        blob = self._class_bytes(name)
        if blob is None:
            raise KeyError("class %s is not in %s" % (name, self.jar))
        if name not in self._written:
            target = self._tmp / (name.replace(".", "/") + ".class")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            self._written.add(name)
        result = subprocess.run(
            ["javap", "-p", "-c", "-cp", str(self._tmp), name],
            capture_output=True, text=True)
        if result.returncode != 0:
            raise UnsupportedBytecode(result.stderr.strip() or ("javap failed on %s" % cls))
        self._text[name] = result.stdout
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
