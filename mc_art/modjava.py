"""The mod side of an entity: the Java model class, and reading it back.

When the model already exists, 'mc-art model' reads it out of bytecode. When you
are making the mod, the model is yours to write -- and then the layout and the
Java have to agree, or the atlas is painted into rectangles nothing reads.

Rather than asking a human to keep two files in step, this module makes the spec
the single source and writes the Java from it. Reading it back closes the loop:
emit, edit if you like, read, and compare. A drift between the atlas and the
code becomes a diff rather than a debugging session.
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = ["class_name_from", "emit", "parse"]

_MODEL_BASE = "net.minecraft.client.model.ModelBase"
_RENDERER = "net.minecraft.client.model.ModelRenderer"


def class_name_from(path: Any) -> str:
    """ModelBloodSlime.java -> ModelBloodSlime."""
    text = str(path).replace("\\", "/").split("/")[-1]
    return text[:-5] if text.endswith(".java") else text


def _f(value: Any) -> str:
    number = float(value)
    if number == int(number):
        return "%d.0F" % int(number)
    return "%gF" % number


def emit(spec: dict[str, Any], *, class_name: str, package: str = "",
         entity_class: str = "net.minecraft.entity.Entity",
         texture: str | None = None) -> str:
    """Write the 1.12 model class that the atlas was planned against."""
    lines: list[str] = []
    if package:
        lines.append("package %s;" % package)
        lines.append("")
    lines.append("import %s;" % _MODEL_BASE)
    lines.append("import %s;" % _RENDERER)
    lines.append("import %s;" % entity_class)
    lines.append("")
    lines.append("/**")
    lines.append(" * %s" % spec.get("name") or "entity")
    if texture:
        lines.append(" *")
        lines.append(" * texture: %s (%dx%d)" % (texture, spec["tex"][0], spec["tex"][1]))
    lines.append(" *")
    lines.append(" * Generated from the model spec by mc-art. The addBox calls below are the")
    lines.append(" * contract the atlas was planned against: each one's texture net is")
    lines.append(" * 2*depth + 2*width by depth + height at the stated offset. Change one and")
    lines.append(" * the other has to follow, so regenerate rather than hand-edit.")
    lines.append(" */")
    lines.append("public class %s extends ModelBase {" % class_name)
    for part in spec["parts"]:
        lines.append("    public ModelRenderer %s;" % part["name"])
    lines.append("")
    lines.append("    public %s() {" % class_name)
    for part in spec["parts"]:
        name = part["name"]
        for index, box in enumerate(part["boxes"]):
            offset = (box["u"], box["v"]) if index == 0 else (box["u"], box["v"])
            lines.append("        this.%s = new ModelRenderer(this, %d, %d);"
                         % (name, offset[0], offset[1]))
            call = "        this.%s.addBox(%s, %s, %s, %d, %d, %d" % (
                name, _f(box["at"][0]), _f(box["at"][1]), _f(box["at"][2]),
                box["w"], box["h"], box["d"])
            if box.get("inflate"):
                call += ", %s" % _f(box["inflate"])
            lines.append(call + ");")
        px, py, pz = part["pivot"]
        lines.append("        this.%s.setRotationPoint(%s, %s, %s);"
                     % (name, _f(px), _f(py), _f(pz)))
        for axis, value in sorted(part.get("rot", {}).items()):
            lines.append("        this.%s.rotateAngle%s = %sF;"
                         % (name, axis.upper(), _radians(value)))
        lines.append("")
    lines.append("    }")
    lines.append("")
    lines.append("    @Override")
    lines.append("    public void setRotationAngles(float limbSwing, float limbSwingAmount,")
    lines.append("                                  float ageInTicks, float netHeadYaw,")
    lines.append("                                  float headPitch, float scaleFactor, Entity entity) {")
    lines.append("        super.setRotationAngles(limbSwing, limbSwingAmount, ageInTicks,")
    lines.append("                                netHeadYaw, headPitch, scaleFactor, entity);")
    lines.append("    }")
    lines.append("")
    lines.append("    @Override")
    lines.append("    public void render(Entity entity, float limbSwing, float limbSwingAmount,")
    lines.append("                       float ageInTicks, float netHeadYaw, float headPitch,")
    lines.append("                       float scale) {")
    lines.append("        setRotationAngles(limbSwing, limbSwingAmount, ageInTicks,")
    lines.append("                          netHeadYaw, headPitch, scale, entity);")
    for part in spec["parts"]:
        lines.append("        this.%s.render(scale);" % part["name"])
    lines.append("    }")
    lines.append("}")
    return chr(10).join(lines) + chr(10)


def _radians(degrees: Any) -> str:
    """The spec speaks degrees (that is what the renderer speaks); the game
    speaks radians, and Math.PI / 2 is 1.5707964F."""
    return "%.7g" % math.radians(float(degrees))


_RE_FIELD = re.compile(r"ModelRenderer\s+(\w+)\s*[;=]")
_RE_NEW = re.compile(r"(?:this\.)?(\w+)\s*=\s*new\s+ModelRenderer\s*\(\s*this\s*,"
                     r"\s*(-?\d+)\s*,\s*(-?\d+)\s*\)")
_RE_BOX = re.compile(r"(?:this\.)?(\w+)\.addBox\s*\(([^;]*?)\)\s*;", re.S)
_RE_ROT_POINT = re.compile(r"(?:this\.)?(\w+)\.setRotationPoint\s*\(([^;]*?)\)\s*;")
_RE_ANGLE = re.compile(r"(?:this\.)?(\w+)\.rotateAngle([XYZ])\s*=\s*([^;]+?)\s*;")


def _numbers(text: str) -> list[float]:
    values = []
    for token in text.split(","):
        token = token.strip().rstrip("Ff").strip()
        try:
            values.append(float(token))
        except ValueError:
            return []
    return values


def parse(source: str, *, tex: tuple[int, int] = (64, 32),
          name: str = "entity") -> dict[str, Any]:
    """Read a hand-written or generated model class back into a spec.

    Only literal coordinates are recovered. A model that computes its geometry
    at runtime cannot be read this way -- use the compiled class instead, which
    is what 'mc-art model --jar' is for.
    """
    order: list[str] = []
    for match in _RE_FIELD.finditer(source):
        if match.group(1) not in order:
            order.append(match.group(1))

    boxes: dict[str, list[dict[str, Any]]] = {name: [] for name in order}
    offsets: dict[str, tuple[int, int]] = {}
    for match in _RE_NEW.finditer(source):
        part = match.group(1)
        if part not in boxes:
            boxes[part] = []
            order.append(part)
        offsets[part] = (int(match.group(2)), int(match.group(3)))

    for match in _RE_BOX.finditer(source):
        part = match.group(1)
        if part not in boxes:
            continue
        values = _numbers(match.group(2))
        if len(values) not in (6, 7):
            continue
        u, v = offsets.get(part, (0, 0))
        box = {"u": u, "v": v, "at": values[0:3],
               "w": int(values[3]), "h": int(values[4]), "d": int(values[5]),
               "inflate": values[6] if len(values) == 7 else 0.0}
        boxes[part].append(box)

    pivots: dict[str, list[float]] = {}
    for match in _RE_ROT_POINT.finditer(source):
        values = _numbers(match.group(2))
        if len(values) == 3:
            pivots[match.group(1)] = values

    rotations: dict[str, dict[str, float]] = {}
    for match in _RE_ANGLE.finditer(source):
        expression = match.group(3).strip()
        value = None
        literal = _numbers(expression)
        if len(literal) == 1:
            value = literal[0]
        else:
            value = _named_angle(expression)
        if value is None:
            continue
        rotations.setdefault(match.group(1), {})[match.group(2).lower()] = \
            round(math.degrees(value), 4)

    parts = []
    for part in order:
        if not boxes.get(part):
            continue
        parts.append({"name": part, "pivot": pivots.get(part, [0.0, 0.0, 0.0]),
                      "rot": rotations.get(part, {}), "boxes": boxes[part]})
    if not parts:
        raise ValueError("no addBox calls with literal coordinates were found")
    return {"name": name, "tex": list(tex), "parts": parts}


_NAMED = {
    "Math.PI / 2F": math.pi / 2, "Math.PI / 2": math.pi / 2,
    "-(Math.PI / 2F)": -math.pi / 2, "-(Math.PI / 2)": -math.pi / 2,
    "(float)Math.PI / 2F": math.pi / 2, "(float) Math.PI / 2F": math.pi / 2,
    "Math.PI": math.pi, "-(float)Math.PI / 2F": -math.pi / 2,
}


def _named_angle(expression: str):
    """Recognise the shapes a hand-written model actually uses."""
    cleaned = expression.replace(" ", "")
    for text, value in _NAMED.items():
        if cleaned == text.replace(" ", ""):
            return value
    return None
