"""Render a model the way the game does, so a delivery can be looked at in the
game's own view before it is handed over.

Two paths, one rasteriser:

* BLOCK models are data. Elements carry from/to plus a per-face uv, texture and
  rotation, so the JSON is the authority and nothing here is inferred.
* ENTITY models are code. This file is a transcription of the sources --
  ModelBox, TexturedQuad, ModelRenderer, RendererLivingEntity, RenderHelper --
  not a recollection of them. That distinction matters, because a remembered
  version of this rendered a cow that was recognisably a cow and still wrong.

The renderer has one job in the loop: it is the check that a layout does not
merely agree with itself, but actually reads as the thing. It has been wrong
before in ways that looked plausible, so before trusting any render, run the
self test. It stamps a glyph on a known face and both asserts the corner order
arithmetically and writes the picture for the eye.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

__all__ = [
    "Camera",
    "TextureSource",
    "CONTROLS",
    "control_model",
    "fit_camera",
    "orientation_check",
    "render_block_model",
    "render_entity",
    "render_views",
]

FOV_Y = math.radians(45.0)
DEFAULT_VIEWPORT = (800, 560)
BACKGROUND = (28, 26, 30)

# RendererLivingEntity translates by this after scale(-1,-1,1), so model y=24
# (the feet) lands on world y=0.
FOOT_OFFSET = 1.5078125

_LIGHT0 = np.array((0.2, 1.0, -0.7), dtype=np.float64)
_LIGHT0 = _LIGHT0 / np.linalg.norm(_LIGHT0)
_LIGHT1 = np.array((-0.2, 1.0, 0.7), dtype=np.float64)
_LIGHT1 = _LIGHT1 / np.linalg.norm(_LIGHT1)

# Minecraft's own per-face brightness for block models.
FACE_SHADE = {"up": 1.0, "down": 0.5, "north": 0.8, "south": 0.8,
              "east": 0.6, "west": 0.6}


# ---------------------------------------------------------------------------
# the rasteriser
# ---------------------------------------------------------------------------


class Camera:
    """A pinhole camera in the game's world axes: x east, y up, z south."""

    def __init__(self, position, target, viewport=DEFAULT_VIEWPORT, up=(0.0, 1.0, 0.0)):
        self.position = np.array(position, dtype=np.float64)
        forward = np.array(target, dtype=np.float64) - self.position
        self.forward = forward / np.linalg.norm(forward)
        right = np.cross(self.forward, np.array(up, dtype=np.float64))
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, self.forward)
        self.viewport = viewport
        self.focal = (viewport[1] / 2.0) / math.tan(FOV_Y / 2.0)

    def project(self, point):
        offset = np.asarray(point, dtype=np.float64) - self.position
        x = float(np.dot(offset, self.right))
        y = float(np.dot(offset, self.up))
        z = float(np.dot(offset, self.forward))
        if z <= 0.02:
            return None
        return (self.viewport[0] / 2.0 + x * self.focal / z,
                self.viewport[1] / 2.0 - y * self.focal / z, z)


def _barycentric(screen, depth):
    """Shared front end of both raster paths: the covered pixel grid and its
    perspective-correct depth, uv and texel lookup."""
    (sx0, sy0, z0), (sx1, sy1, z1), (sx2, sy2, z2) = screen
    height, width = depth.shape
    minx = max(0, int(math.floor(min(sx0, sx1, sx2))))
    maxx = min(width - 1, int(math.ceil(max(sx0, sx1, sx2))))
    miny = max(0, int(math.floor(min(sy0, sy1, sy2))))
    maxy = min(height - 1, int(math.ceil(max(sy0, sy1, sy2))))
    if minx > maxx or miny > maxy:
        return None
    area = (sx1 - sx0) * (sy2 - sy0) - (sx2 - sx0) * (sy1 - sy0)
    if abs(area) < 1e-9:
        return None
    xs = np.arange(minx, maxx + 1, dtype=np.float64) + 0.5
    ys = np.arange(miny, maxy + 1, dtype=np.float64) + 0.5
    gx, gy = np.meshgrid(xs, ys)
    w0 = ((sx1 - gx) * (sy2 - gy) - (sx2 - gx) * (sy1 - gy)) / area
    w1 = ((sx2 - gx) * (sy0 - gy) - (sx0 - gx) * (sy2 - gy)) / area
    w2 = 1.0 - w0 - w1
    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not inside.any():
        return None
    inv = w0 / z0 + w1 / z1 + w2 / z2
    with np.errstate(divide="ignore", invalid="ignore"):
        zs = np.where(inv > 1e-9, 1.0 / inv, np.inf)
    return minx, maxx, miny, maxy, inside, zs, inv, w0, w1, w2


def raster(colour, depth, camera, texture, points, uvs, shade, alpha_test=True):
    """Opaque path: nearest sampling with a 0.5 alpha test (the game's default)."""
    screen = []
    for point in points:
        projected = camera.project(point)
        if projected is None:
            return
        screen.append(projected)
    grid = _barycentric(screen, depth)
    if grid is None:
        return
    minx, maxx, miny, maxy, inside, zs, inv, w0, w1, w2 = grid
    (_, _, z0), (_, _, z1), (_, _, z2) = screen
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (w0 * uvs[0][0] / z0 + w1 * uvs[1][0] / z1 + w2 * uvs[2][0] / z2) / inv
        v = (w0 * uvs[0][1] / z0 + w1 * uvs[1][1] / z1 + w2 * uvs[2][1] / z2) / inv
    tx = np.clip((u * texture.shape[1]).astype(np.int32), 0, texture.shape[1] - 1)
    ty = np.clip((v * texture.shape[0]).astype(np.int32), 0, texture.shape[0] - 1)
    texel = texture[ty, tx]
    window = depth[miny:maxy + 1, minx:maxx + 1]
    keep = inside & (zs < window)
    if alpha_test:
        keep &= texel[:, :, 3] >= 0.5
    if not keep.any():
        return
    window[keep] = zs[keep]
    target = colour[miny:maxy + 1, minx:maxx + 1, :]
    target[keep, :3] = texel[keep, :3] * shade
    target[keep, 3] = 1.0


def raster_blend(colour, depth, camera, texture, points, uvs, shade):
    """Translucent path for a layer drawn with blending, such as a slime's gel.

    LayerSlimeGel leaves DEPTH WRITES ON. Omitting the write feeds every back
    face through and stacks all of them, which is what turned a translucent
    slime black.
    """
    screen = []
    for point in points:
        projected = camera.project(point)
        if projected is None:
            return
        screen.append(projected)
    grid = _barycentric(screen, depth)
    if grid is None:
        return
    minx, maxx, miny, maxy, inside, zs, inv, w0, w1, w2 = grid
    (_, _, z0), (_, _, z1), (_, _, z2) = screen
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (w0 * uvs[0][0] / z0 + w1 * uvs[1][0] / z1 + w2 * uvs[2][0] / z2) / inv
        v = (w0 * uvs[0][1] / z0 + w1 * uvs[1][1] / z1 + w2 * uvs[2][1] / z2) / inv
    tx = np.clip((u * texture.shape[1]).astype(np.int32), 0, texture.shape[1] - 1)
    ty = np.clip((v * texture.shape[0]).astype(np.int32), 0, texture.shape[0] - 1)
    texel = texture[ty, tx]
    window = depth[miny:maxy + 1, minx:maxx + 1]
    keep = inside & (zs <= window) & (texel[:, :, 3] > 0.0)
    if not keep.any():
        return
    window[keep] = zs[keep]
    target = colour[miny:maxy + 1, minx:maxx + 1, :]
    alpha = texel[keep, 3:4]
    target[keep, :3] = texel[keep, :3] * shade * alpha + target[keep, :3] * (1.0 - alpha)
    target[keep, 3] = 1.0


def _canvas(camera, background=BACKGROUND):
    width, height = camera.viewport
    colour = np.zeros((height, width, 4), dtype=np.float32)
    colour[:, :, :3] = np.array(background, dtype=np.float32) / 255.0
    colour[:, :, 3] = 1.0
    depth = np.full((height, width), np.inf, dtype=np.float64)
    return colour, depth


def _save(colour, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.clip(colour * 255.0, 0, 255).astype(np.uint8), "RGBA").save(path)
    return str(path)


# ---------------------------------------------------------------------------
# textures, from a jar or a directory
# ---------------------------------------------------------------------------


def image_from_bytes(blob, tint=(1.0, 1.0, 1.0)):
    array = np.asarray(Image.open(io.BytesIO(blob)).convert("RGBA"),
                       dtype=np.float32) / 255.0
    array = array.copy()
    array[:, :, :3] *= np.array(tint, dtype=np.float32)
    return array


class TextureSource:
    """Find a texture by its path, in a jar or an asset directory.

    Vanilla art is deliberately not vendored into this skill; point it at the
    game jar, a mod jar, or an unpacked asset root.
    """

    def __init__(self, path, namespace="minecraft"):
        self.path = Path(path)
        self.namespace = namespace
        self._zip = None
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self._zip = zipfile.ZipFile(self.path)
        elif not self.path.is_dir():
            raise FileNotFoundError(self.path)

    def read(self, relative):
        relative = str(relative).lstrip("/")
        candidates = []
        if self._zip is not None:
            candidates.append("assets/%s/%s" % (self.namespace, relative))
            candidates.append(relative)
            for name in candidates:
                try:
                    return self._zip.read(name)
                except KeyError:
                    continue
            return None
        for base in (self.path, self.path / "assets" / self.namespace, self.path / "assets"):
            candidate = base / relative
            if candidate.is_file():
                return candidate.read_bytes()
        return None

    def image(self, relative, tint=(1.0, 1.0, 1.0)):
        blob = self.read(relative)
        if blob is None:
            raise FileNotFoundError("%s is not in %s" % (relative, self.path))
        return image_from_bytes(blob, tint)

    def close(self):
        if self._zip is not None:
            self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


# ---------------------------------------------------------------------------
# entity models
# ---------------------------------------------------------------------------


def to_world(point):
    """Model units to game blocks. RendererLivingEntity does disableCull,
    rotate(180 - yaw), scale(-1,-1,1), translate(0,-1.5078125,0); at yaw 0 a
    model point (mx,my,mz) lands at (mx, 1.5078125-my, -mz)/16, which is the
    same world the block renderer uses."""
    mx, my, mz = point
    return (mx / 16.0, FOOT_OFFSET - my / 16.0, -mz / 16.0)


def entity_shade(normal):
    """RenderHelper.enableStandardItemLighting: ambient 0.4 plus two white 0.6
    lights, flat shaded with the quad's own normal."""
    dot0 = max(0.0, float(np.dot(normal, _LIGHT0)))
    dot1 = max(0.0, float(np.dot(normal, _LIGHT1)))
    return 0.4 + 0.6 * (dot0 + dot1)


def rotate_point(point, pivot, axis, degrees):
    if not degrees:
        return point
    angle = math.radians(degrees)
    p = np.array(point, dtype=np.float64) - np.array(pivot, dtype=np.float64)
    c, s = math.cos(angle), math.sin(angle)
    if axis == "x":
        r = np.array([p[0], p[1] * c - p[2] * s, p[1] * s + p[2] * c])
    elif axis == "y":
        r = np.array([p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c])
    else:
        r = np.array([p[0] * c - p[1] * s, p[0] * s + p[1] * c, p[2]])
    return tuple(r + np.array(pivot, dtype=np.float64))


def place(point, pivot, rotation):
    """pivot + Rz*Ry*Rx*point. ModelRenderer.render translates to the rotation
    point and THEN rotates, so the rotation is about the part's local origin;
    rotating about the pivot instead leaves a body floating and swallows legs."""
    origin = (0.0, 0.0, 0.0)
    for axis in ("z", "y", "x"):
        point = rotate_point(point, origin, axis, rotation.get(axis, 0.0))
    return tuple(np.array(point, dtype=np.float64) + np.array(pivot, dtype=np.float64))


def corners(x1, y1, z1, x2, y2, z2):
    return {"A": (x1, y1, z1), "B": (x2, y1, z1), "C": (x2, y2, z1), "D": (x1, y2, z1),
            "E": (x1, y1, z2), "F": (x2, y1, z2), "G": (x2, y2, z2), "H": (x1, y2, z2)}


# name, the corner keys in ModelBox's own quadList order, and the texture rect
# relative to the box's own offset. The bottom rect really is (v+d, v) -- a
# flipped v pairs with TexturedQuad's corner order and comes out upright.
QUADS = (
    ("right", "FBCG", lambda u, v, w, h, d: (u + d + w, v + d, u + d + w + d, v + d + h)),
    ("left", "AEHD", lambda u, v, w, h, d: (u, v + d, u + d, v + d + h)),
    ("top", "FEAB", lambda u, v, w, h, d: (u + d, v, u + d + w, v + d)),
    ("bottom", "CDHG", lambda u, v, w, h, d: (u + d + w, v + d, u + d + w + w, v)),
    ("front", "BADC", lambda u, v, w, h, d: (u + d, v + d, u + d + w, v + d + h)),
    ("back", "EFGH", lambda u, v, w, h, d: (u + 2 * d + w, v + d, u + 2 * d + w + w, v + d + h)),
)


def iter_quads(model, pose=None):
    """Every box quad as (face, world corners, uvs, world normal)."""
    pose = pose or {}
    tex_w, tex_h = model.get("tex", (64, 32))
    for part in model["parts"]:
        pivot = part["pivot"]
        rotation = dict(part.get("rot", {}), **pose.get(part["name"], {}))
        for box in part["boxes"]:
            ox, oy, oz = box["at"]
            w, h, d = box["w"], box["h"], box["d"]
            inflate = box.get("inflate", 0.0)
            table = corners(ox - inflate, oy - inflate, oz - inflate,
                            ox + w + inflate, oy + h + inflate, oz + d + inflate)
            for name, keys, rect_of in QUADS:
                u1, v1, u2, v2 = rect_of(box["u"], box["v"], w, h, d)
                model_corners = [place(table[key], pivot, rotation) for key in keys]
                world = [to_world(point) for point in model_corners]
                normal = np.cross(np.array(model_corners[2]) - np.array(model_corners[1]),
                                  np.array(model_corners[0]) - np.array(model_corners[1]))
                normal = to_world(normal)
                normal = normal / max(np.linalg.norm(normal), 1e-9)
                # TexturedQuad ctor: [0]=(u2,v1) [1]=(u1,v1) [2]=(u1,v2) [3]=(u2,v2)
                pairs = ((u2, v1), (u1, v1), (u1, v2), (u2, v2))
                uvs = [(pair[0] / tex_w, pair[1] / tex_h) for pair in pairs]
                yield name, world, uvs, normal


def scene_bbox(models):
    points = []
    for model in models:
        for _, world, _, _ in iter_quads(model):
            points.extend(world)
    array = np.array(points)
    return array.min(axis=0), array.max(axis=0)


def fit_camera(models, direction, viewport=DEFAULT_VIEWPORT, margin=0.86):
    """The nearest distance along direction that keeps the whole model in frame."""
    if not isinstance(models, (list, tuple)):
        models = [models]
    low, high = scene_bbox(models)
    centre = (low + high) / 2.0
    vector = np.array(direction, dtype=np.float64)
    vector = vector / np.linalg.norm(vector)
    box = [np.array([x, y, z]) for x in (low[0], high[0])
           for y in (low[1], high[1]) for z in (low[2], high[2])]
    radius = float(np.linalg.norm(high - low))
    half_w = viewport[0] * (1.0 - margin) / 2.0
    half_h = viewport[1] * (1.0 - margin) / 2.0
    for step in range(1, 8000):
        distance = radius * step / 400.0
        camera = Camera(tuple(centre + vector * distance), tuple(centre), viewport=viewport)
        if all(_framed(camera.project(point), viewport, half_w, half_h) for point in box):
            return camera
    raise RuntimeError("could not frame the model")


def _framed(projected, viewport, half_w, half_h):
    if projected is None:
        return False
    x, y, _ = projected
    return half_w <= x <= viewport[0] - half_w and half_h <= y <= viewport[1] - half_h


def render_entity(items, source, path, camera, background=BACKGROUND, pose=None):
    """Items are (model, texture_path, tint[, blend]) drawn into one z-buffer in
    order, which is the order RendererLivingEntity uses for a model and its
    layers."""
    colour, depth = _canvas(camera, background)
    for item in items:
        model, texture_path, tint = item[0], item[1], item[2]
        blend = item[3] if len(item) > 3 else False
        texture = source.image(texture_path, tint)
        for _, world, uvs, normal in iter_quads(model, pose):
            shade = entity_shade(normal)
            for indices in ((0, 1, 2), (0, 2, 3)):
                points = [world[i] for i in indices]
                coords = [uvs[i] for i in indices]
                if blend:
                    raster_blend(colour, depth, camera, texture, points, coords, shade)
                else:
                    raster(colour, depth, camera, texture, points, coords, shade)
    return _save(colour, path)


# ---------------------------------------------------------------------------
# block models
# ---------------------------------------------------------------------------


def load_block_model(source, name):
    """Resolve a block model and its parent chain from the asset source."""
    return _load_block(source, "models/block/%s.json" % name, set())


def _load_block(source, relative, seen):
    if relative in seen:
        raise ValueError("model parent cycle at %s" % relative)
    seen.add(relative)
    blob = source.read(relative)
    if blob is None:
        raise FileNotFoundError(relative)
    model = json.loads(blob.decode("utf-8"))
    parent = model.get("parent")
    if not parent:
        return model
    parent_relative = "models/%s.json" % parent
    parent_model = _load_block(source, parent_relative, seen)
    merged = dict(parent_model)
    merged.update({key: value for key, value in model.items() if key != "parent"})
    merged["textures"] = dict(parent_model.get("textures", {}), **model.get("textures", {}))
    return merged


# wiki vertex order per face; the uv pattern is (u1,v1),(u1,v2),(u2,v2),(u2,v1)
def block_face_corners(x1, y1, z1, x2, y2, z2, face):
    if face == "down":
        return [(x1, y1, z2), (x1, y1, z1), (x2, y1, z1), (x2, y1, z2)]
    if face == "up":
        return [(x1, y2, z1), (x1, y2, z2), (x2, y2, z2), (x2, y2, z1)]
    if face == "north":
        return [(x2, y2, z1), (x2, y1, z1), (x1, y1, z1), (x1, y2, z1)]
    if face == "south":
        return [(x1, y2, z2), (x1, y1, z2), (x2, y1, z2), (x2, y2, z2)]
    if face == "west":
        return [(x1, y2, z1), (x1, y1, z1), (x1, y1, z2), (x1, y2, z2)]
    return [(x2, y2, z2), (x2, y1, z2), (x2, y1, z1), (x2, y2, z1)]


# 1.13 moved block art from textures/blocks to textures/block; a jar or a mod
# can be either, so resolve the reference against every plausible directory
# rather than assuming the era.
BLOCK_TEXTURE_DIRS = ("textures/block", "textures/blocks", "textures/items",
                      "textures/item", "textures/entity", "textures")


def resolve_block_texture(model, reference, source):
    name = str(reference)
    for _ in range(8):
        if not name.startswith("#"):
            break
        name = str(model.get("textures", {}).get(name[1:], ""))
    if not name:
        raise KeyError("unresolved texture reference %r" % (reference,))
    base = name[:-4] if name.endswith(".png") else name
    candidates = [name] if name.endswith(".png") else []
    for directory in BLOCK_TEXTURE_DIRS:
        candidates.append("%s/%s.png" % (directory, base))
        candidates.append("%s/%s.png" % (directory, base.split("/")[-1]))
    for candidate in candidates:
        blob = source.read(candidate)
        if blob is not None:
            return image_from_bytes(blob)
    raise FileNotFoundError("%s (from %r) is not in %s" % (name, reference, source.path))


def render_block_model(model, source, path, camera, background=BACKGROUND, scale=16.0):
    colour, depth = _canvas(camera, background)
    for element in model.get("elements", []):
        x1, y1, z1 = element["from"]
        x2, y2, z2 = element["to"]
        rotation = element.get("rotation")
        for face, data in element.get("faces", {}).items():
            points = block_face_corners(x1, y1, z1, x2, y2, z2, face)
            if rotation:
                points = [rotate_point(point, rotation["origin"],
                                       rotation.get("axis", "y"), rotation.get("angle", 0))
                          for point in points]
            points = [(p[0] / scale, p[1] / scale, p[2] / scale) for p in points]
            u1, v1, u2, v2 = data.get("uv") or [0, 0, 16, 16]
            base = [(u1, v1), (u1, v2), (u2, v2), (u2, v1)]
            steps = int(data.get("rotation", 0)) // 90
            uvs = [(base[(i - steps) % 4][0] / 16.0, base[(i - steps) % 4][1] / 16.0)
                   for i in range(4)]
            texture = resolve_block_texture(model, data.get("texture", "#particle"), source)
            shade = FACE_SHADE[face]
            for indices in ((0, 1, 2), (0, 2, 3)):
                raster(colour, depth, camera, texture,
                       [points[i] for i in indices], [uvs[i] for i in indices], shade)
    return _save(colour, path)


# ---------------------------------------------------------------------------
# views and the shipped controls
# ---------------------------------------------------------------------------

# The camera looks from this direction (game axes). A model with yaw 0 faces
# south, so "front" sits on the +z side.
VIEWS = {
    "front34": (0.95, 0.55, 1.0),
    "front": (0.22, 0.30, 1.0),
    "side": (1.0, 0.28, 0.10),
    "back34": (-0.95, 0.55, -1.0),
    "top34": (0.85, 1.2, 0.85),
}


def _quadruped(texture, head_box, body_box, leg_box):
    head = {"name": "head", "pivot": (0, 6, -8), "rot": {}, "boxes": [head_box]}
    body = {"name": "body", "pivot": (0, 5, 2), "rot": {"x": 90.0}, "boxes": [body_box]}
    legs = []
    for name, (px, pz) in (("leg1", (-3, 7)), ("leg2", (3, 7)),
                           ("leg3", (-3, -5)), ("leg4", (3, -5))):
        legs.append({"name": name, "pivot": (px, 12, pz), "rot": {},
                     "boxes": [dict(leg_box)]})
    return {"texture": texture, "tex": (64, 32), "parts": [head, body] + legs}


def cow():
    """ModelCow, which shifts its legs and keeps its udder on the body renderer."""
    head = {"name": "head", "pivot": (0, 4, -8), "rot": {}, "boxes": [
        {"u": 0, "v": 0, "at": (-4, -4, -6), "w": 8, "h": 8, "d": 6},
        {"u": 22, "v": 0, "at": (-5, -5, -4), "w": 1, "h": 3, "d": 1},
        {"u": 22, "v": 0, "at": (4, -5, -4), "w": 1, "h": 3, "d": 1}]}
    body = {"name": "body", "pivot": (0, 5, 2), "rot": {"x": 90.0}, "boxes": [
        {"u": 18, "v": 4, "at": (-6, -10, -7), "w": 12, "h": 18, "d": 10},
        {"u": 52, "v": 0, "at": (-2, 2, -8), "w": 4, "h": 6, "d": 1}]}
    legs = []
    for name, (px, pz) in (("leg1", (-4, 7)), ("leg2", (4, 7)),
                           ("leg3", (-4, -6)), ("leg4", (4, -6))):
        legs.append({"name": name, "pivot": (px, 12, pz), "rot": {}, "boxes": [
            {"u": 0, "v": 16, "at": (-2, 0, -2), "w": 4, "h": 12, "d": 4}]})
    return {"texture": "textures/entity/cow/cow.png", "tex": (64, 32),
            "parts": [head, body] + legs}


def sheep_skin():
    """ModelSheep2, the sheared sheep."""
    return _quadruped(
        "textures/entity/sheep/sheep.png",
        {"u": 0, "v": 0, "at": (-3, -4, -6), "w": 6, "h": 6, "d": 8},
        {"u": 28, "v": 8, "at": (-4, -10, -7), "w": 8, "h": 16, "d": 6},
        {"u": 0, "v": 16, "at": (-2, 0, -2), "w": 4, "h": 12, "d": 4})


def sheep_wool():
    """ModelSheep1, the fleece. NOT the skin inflated: its head is 6 deep, not
    8, and its legs are half height, which is why the fur atlas has a 24x12 head
    net and a 16x10 leg net."""
    return _quadruped(
        "textures/entity/sheep/sheep_fur.png",
        {"u": 0, "v": 0, "at": (-3, -4, -4), "w": 6, "h": 6, "d": 6, "inflate": 0.6},
        {"u": 28, "v": 8, "at": (-4, -10, -7), "w": 8, "h": 16, "d": 6, "inflate": 1.75},
        {"u": 0, "v": 16, "at": (-2, 0, -2), "w": 4, "h": 6, "d": 4, "inflate": 0.5})


def slime_core():
    """ModelSlime(16): the opaque 6x6x6 core plus the eyes and mouth."""
    parts = [{"name": "body", "pivot": (0, 0, 0), "rot": {}, "boxes": [
        {"u": 0, "v": 16, "at": (-3, 17, -3), "w": 6, "h": 6, "d": 6}]}]
    for name, (u, v, at) in (("eyeRight", (32, 0, (-3.25, 18, -3.5))),
                             ("eyeLeft", (32, 4, (1.25, 18, -3.5))),
                             ("mouth", (32, 8, (0, 21, -3.5)))):
        size = 2 if name != "mouth" else 1
        parts.append({"name": name, "pivot": (0, 0, 0), "rot": {}, "boxes": [
            {"u": u, "v": v, "at": at, "w": size, "h": size, "d": size}]})
    return {"texture": "textures/entity/slime/slime.png", "tex": (64, 32), "parts": parts}


def slime_shell():
    """ModelSlime(0): the 8x8x8 gel, translucent, drawn over the core."""
    return {"texture": "textures/entity/slime/slime.png", "tex": (64, 32), "parts": [
        {"name": "gel", "pivot": (0, 0, 0), "rot": {}, "boxes": [
            {"u": 0, "v": 0, "at": (-4, 16, -4), "w": 8, "h": 8, "d": 8}]}]}


# name -> layers, each (model factory, blend). Order matters: opaque first.
CONTROLS = {
    "cow": (("cow", cow, False),),
    "sheep": (("sheep", sheep_skin, False),),
    "wool": (("wool", sheep_wool, False),),
    "both": (("sheep", sheep_skin, False), ("wool", sheep_wool, False)),
    "slime": (("core", slime_core, False), ("gel", slime_shell, True)),
}

CONTROL_VIEWS = ("front34", "side")


def control_model(name):
    """One named control as a single-layer model, for callers that want to see
    the boxes rather than the picture."""
    layers = CONTROLS[name]
    return layers[0][1]()


def render_views(name, source, out_dir, views=CONTROL_VIEWS, viewport=DEFAULT_VIEWPORT):
    """Render a shipped control. Returns the written paths."""
    layers = CONTROLS[name]
    models = [factory() for _, factory, _ in layers]
    written = []
    for view in views:
        camera = fit_camera(models, VIEWS[view], viewport=viewport)
        if len(layers) == 1:
            items = [(models[0], models[0]["texture"], (1.0, 1.0, 1.0))]
        else:
            items = [(model, model["texture"], (1.0, 1.0, 1.0), blend)
                     for model, (_, _, blend) in zip(models, layers)]
        written.append(render_entity(items, source, Path(out_dir) / ("%s_%s.png" % (name, view)),
                                     camera))
    return written


# ---------------------------------------------------------------------------
# the self test
# ---------------------------------------------------------------------------

GLYPH = ("XXXXXX",
         "X.....",
         "X.....",
         "XXXX..",
         "X.....",
         "X.....")


def glyph_texture(path):
    """A 64x32 sheet with an F stamped into the head's front rect, (8,8)-(14,14)."""
    texture = np.full((32, 64, 4), 255, dtype=np.uint8)
    for row, line in enumerate(GLYPH):
        for column, char in enumerate(line):
            if char == "X":
                texture[8 + row, 8 + column] = (0, 0, 0, 255)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(texture, "RGBA").save(path)
    return str(path)


def glyph_model(texture_path):
    """Only the head, so the F is the whole picture."""
    model = _quadruped(
        texture_path,
        {"u": 0, "v": 0, "at": (-3, -4, -6), "w": 6, "h": 6, "d": 8},
        {"u": 28, "v": 8, "at": (-4, -10, -7), "w": 8, "h": 16, "d": 6},
        {"u": 0, "v": 16, "at": (-2, 0, -2), "w": 4, "h": 12, "d": 4})
    model["parts"] = model["parts"][:1]
    return model


def orientation_check():
    """Assert the corner order without rendering anything.

    In a head-on front view, the model corner that lands top-left on screen must
    carry the texture rect's top-left uv. Get TexturedQuad's assignment wrong and
    every face quietly rotates 180 degrees and mirrors -- a cow still looks like
    a cow, which is exactly why this has to be an assertion and not an eyeball.
    """
    model = glyph_model("unused.png")
    camera = Camera((0.0, FOOT_OFFSET, 4.0), (0.0, FOOT_OFFSET, 0.0), viewport=(200, 200))
    for face, world, uvs, _normal in iter_quads(model):
        if face != "front":
            continue
        screen = [camera.project(point) for point in world]
        if any(point is None for point in screen):
            return False, "the front quad does not project"
        top_left = min(range(4), key=lambda i: screen[i][0] + screen[i][1])
        u, v = uvs[top_left]
        # the head's front rect is (u+d, v+d)-(u+d+w, v+d+h) = (8,8)-(14,14)
        if abs(u - 8.0 / 64.0) > 1e-9 or abs(v - 8.0 / 32.0) > 1e-9:
            return False, ("the top-left screen corner carries uv (%.4f, %.4f), "
                           "not the rect's top-left (%.4f, %.4f)"
                           % (u, v, 8.0 / 64.0, 8.0 / 32.0))
        return True, "the top-left screen corner carries the rect's top-left uv"
    return False, "no front quad was produced"


def selftest(source, out_dir, viewport=DEFAULT_VIEWPORT):
    """Write the F control and report whether the orientation assertion holds."""
    out_dir = Path(out_dir)
    texture = glyph_texture(out_dir / "glyph.png")
    model = glyph_model(texture)
    source = TextureSource(out_dir) if source is None else source
    written = []
    for view in ("front", "top34"):
        camera = fit_camera(model, VIEWS[view], viewport=viewport)
        written.append(render_entity([(model, "glyph.png", (1.0, 1.0, 1.0))],
                                     source, out_dir / ("selftest_%s.png" % view), camera))
    ok, detail = orientation_check()
    return ok, detail, written
