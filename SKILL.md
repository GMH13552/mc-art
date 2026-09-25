---
name: mc-art
description: Use when generating or editing Minecraft pixel art (item/block/entity textures, resource packs), matching a mod's or vanilla's existing style, auditing generated sprites frame-by-frame, or rendering a model the way the game does to check a delivery. A deterministic engine that scans asset roots, turns images into evidence, rasterises an authored plan and measures the result — every design decision is the caller's.
---

# Minecraft Art Studio

A **deterministic engine plus a procedure**. It contains no model: it scans
asset roots, turns pixels into text, rasterises a plan you write, and measures
what came out. Everything needing judgement — what the thing should look like,
which reference matters, whether the render is any good — is yours, and you can
iterate in about a second per attempt.

This skill used to be a wrapper around an API pipeline that called a weaker
model for routing, planning, drawing and review. That pipeline is gone; what
survived is the part that was never a model's job.

```
bin/mc-art          the whole tool surface
mc_art/             the engine (17 modules, Pillow + stdlib)
examples/           a working plan to start from
layouts/            shipped vanilla UV layouts
scripts/            magnify / review_family / continuity
tests/              60 tests over the engine
```

## The loop

```bash
M=/home/gmh/.dsh/skills/mc-art/bin/mc-art
JAR="<path to a vanilla jar, a mod jar, or a directory with assets/>"

# 1. SCAN — what logical names exist (one name = all of its textures)
$M list-groups --source "$JAR" --filter bow --limit 5

# 2. LOOK — pull the real PNGs and read them with the image reader.
#    Never design from a text summary alone.
$M list-groups --source "$JAR" --extract minecraft:item/bow --to /tmp/refs

# 3. EVIDENCE — when you need pixel precision: the literal per-pixel text,
#    and which frame of a family answers this request
$M evidence --source "$JAR" --name bow --member bow_standby

# 4. AUTHOR a plan (your decisions), then RASTERISE it
$M render --plan my.plan.json --out outputs/mine

# 5. LOOK at outputs/mine/sprite.png — then go back to 4
#
# 6. For an asset whose faces differ (a log, a machine, a plant), assemble it:
#    $M pack --manifest pack.json --out pack/     see "Traps" #3
#
# 7. For an ENTITY: one description, two branches. See "Entities", below.
#    Branch A -- the model exists:
#      $M model --jar "$JAR" --texture textures/entity/sheep/sheep.png --emit-spec work/model.json
#    Branch B -- the model does not, so write work/model.json yourself:
#      $M entity --spec work/model.json --out work          # assigns u/v, emits layout.json
#    Then either way, once the atlas is painted:
#      $M entity --spec work/model.json --out work --atlas work/atlas.png   # PASS / FAIL
#      $M ingame --model work/model.json --texture work/atlas.png --out work/view.png
#
# 8. BEFORE HANDING OVER: READ work/view.png. An atlas can land on exactly the
#    right rectangles and the mob still be wrong.
#      $M ingame --selftest --out /tmp/look      # asserts the renderer itself
```

Step 4 takes about a second and needs no credentials. Iterate as long as the
art needs: palette, sampling, mask shapes, highlight placement.

## The plan

Start from `examples/bow_standby.plan.json`. Five keys; three matter:

| key | what it is |
|---|---|
| `request` | `{form, name, namespace, width, height, seed, pack_format}` — the output contract |
| `descriptor` | what you are making: `target`, `semantic`, `visual_identity`, `parts`, `shape_edit_mode`, `reference_strategy` |
| `geometry` | the masks: `parts` + `primitives`. A part with no primitive is **paint-only** — carried by the support under it |
| `appearance` | `palette`, per-part `colors` / `material` / `shade_axis` / `marks`, the sampling keys, `pixel_map`, `outline_*` |
| `references` | the images the appearance samples from: `name`, `path`, `roles`, `notes` |

Two levers decide most outcomes:

- **`shape_edit_mode`** — `appearance_only` / `preserve_silhouette` means the
  source contour *is* the answer: the renderer conforms your masks to the
  reference alpha exactly, so you never hand-draw a 16-row mask. Use
  `new_silhouette` / `local_silhouette_edit` when the contour really changes.
- **`part_reference_sampling`** — `pattern` keeps the source's value rhythm
  (grain, mottling) and retints it toward your ramp while retaining roughly 40%
  of the source's own colour; `value` transfers brightness only; `none` paints
  from your ramp alone. **This is what decides whether the source material
  shows through.** Measured: a bright crystal palette over the vanilla wooden
  bow stayed dark wood-green with `pattern`, and read as translucent crystal
  with `value`.

`reference_sampling` sets the default; `part_reference_sampling` overrides per
part, and `part_reference_sources` binds a part to one named reference.

## After every render: look at the image

Never hand back a render you have not looked at. The numbers have been wrong in
both directions; the sprite is the evidence.

```bash
S=/home/gmh/.dsh/skills/mc-art/scripts
python3 "$S/magnify.py" '{"out":"/tmp/check.png","cols":4,"entries":[
  ["SRC","/tmp/refs/bow_standby.png"], ["MINE","outputs/mine/sprite.png"]]}'
$M measure mine_a.png mine_b.png mine_c.png --baseline /tmp/refs/bow_standby.png
```

Read `/tmp/check.png` back with the image reader — never describe it from the
JSON, a previous run, or memory. Then answer:

- **Contour** — does it match the source frame you conformed to (`IoU 1.0000`,
  `identical`)? In a family, each frame matches *its own* source frame, never
  "vanilla" in general.
- **Colour and texture across frames** — a shared palette is not enough. In
  `measure`: low `agreement` with high `near_agreement` means one palette but
  the shading, outline or highlights moved between frames; low `near_agreement`
  too means the palettes drifted. The **source family's own numbers are the
  bar, not 1.0** — vanilla's own bow frames agree on only 29–66% of their union.
- **State legibility** — can you tell idle from fully drawn? A family where
  nothing changes is as wrong as one where everything does.
- **Material honesty** — no wood showing through crystal, no stone through
  cloth. Counting warm pixels against a cool palette settles it.
- **Failures** — anything that did not render: say what stage died and why.

Fix before reporting, and say what you changed.

## Before you hand it over: look at it in the game's view

An atlas can land on exactly the right rects and the mob can still be wrong: a
layer that shows through, a part buried inside another part, a face nobody ever
sees, a body that reads as a slab. Nothing above catches any of that, because
everything above is flat. So the last look is a render of the model itself.

```bash
$M ingame --selftest --out /tmp/look              # assert the renderer, then look
$M ingame --control cow --control both --source game.jar --out /tmp/look
$M model --jar game.jar --texture textures/entity/sheep/sheep.png --emit-spec mine.json
$M ingame --model mine.json --source game.jar --view front34 --out /tmp/look/mine.png
$M ingame --block anvil_undamaged --source game.jar --out /tmp/look/anvil.png
```

Then read the PNG back with the image reader. A render nobody looked at is not a
check, and "it should be fine" is not a delivery.

### Render it the game's way, then read the picture back

That part is not optional. What *is* optional is rendering a vanilla asset every
single time: do that when the renderer is new, when it changed, or when a render
looks off. It is a calibration, not a ritual — this renderer has been wrong four
separate times and every time it produced a picture that looked plausible, so
when in doubt spend the four seconds and look at the vanilla cow first.

`--selftest` is the cheap half and is worth running every time, because it is
arithmetic rather than a picture. `--control` is the expensive half: look at it
when it matters.

### The ways this renderer lied, so they are not re-learned

| the bug | what it looked like |
|---|---|
| the quad read top-left-then-clockwise instead of `TexturedQuad`'s `(u2,v1) (u1,v1) (u1,v2) (u2,v2)` | every face rotated 180 degrees and mirrored -- a smeared cow that was still recognisably a cow |
| a part rotated *about* its pivot instead of translated to it and rotated about its own origin | the cow's body floating off its legs, two thirds of the sheep's legs swallowed by the body |
| a private left-handed camera, instead of the game's own axes | the whole scene subtly wrong, mobs facing the wrong way |
| a translucent layer blended with depth writes off | a slime turned black: every back face came through and stacked |
| the bytecode stores radians and the renderer speaks degrees | the torso stands on end: a sheep that still reads as a sheep, with its body pointing at the sky |

One more was not the renderer but the model: the slime was assumed to be one
16-cube, so its six faces were painted where the game samples nothing -- at
`u=16` where vanilla is at `u=0` -- and the old check passed, because
"is the atlas 64x32 and are six faces filled" was the only question it asked.
The question that catches it is **is every opaque texel inside some box rect**.

Two of these were caught by arithmetic, not by the eye. That is what
`--selftest` is for: it stamps a glyph on a known face and asserts that the
model corner landing top-left on screen carries the texture rect's top-left uv,
reporting the uv it actually found when it does not. A test that can only pass is
not a test, so `tests/test_ingame.py` injects the broken order and requires
the failure.

### The spec is generated, never typed

`mc-art model --emit-spec` closes the loop: bytecode to a full render spec
(parts, pivots, boxes and pose) to `mc-art ingame --model`. The spec carries
its own `units` field, so the angle convention is not something to remember, and
the recovery is checked by rendering it — the emitted sheep spec and the shipped
`sheep_skin()` control produce byte-identical images.

That matters because a hand-typed spec fails silently. The one written while
building this lost two of a sheep's four legs and still rendered a perfectly
plausible animal. So `ingame` prints the part inventory before it draws: **count
the parts**, and if the line says four, the picture is being polite to you.

### What the two model paths need

Blocks are data: `--block` reads the element list, the per-face uv, texture
and rotation, and the parent chain, so nothing is guessed. Note that the entry
model matters and versions differ -- 1.12's `anvil.json` holds only geometry
and it is `anvil_undamaged.json` that supplies `#body` and `#top`; block art
also moved from `textures/blocks` to `textures/block` in 1.13, so the
resolver tries both.

Entities are code. `--model` takes
`{"tex":[64,32],"parts":[{"name","pivot","rot","boxes":[{"u","v","at","w","h","d","inflate"}]}]}`,
where `at` is `addBox`'s offset, `pivot` is `SetRotationPoint` and `inflate` is the
delta. `mc-art model` recovers all of that from bytecode except the pose:
`rot` is per-frame, set in `setRotationAngles`, and the shipped controls carry
the one constant that matters (a quadruped torso is `body.rotateAngleX = 90`,
which is why forgetting it lays the cow out flat).

Mind the axes, because that is where the fourth bug lived: model space is
**y down, z backward**, one unit is 1/16 block, and the renderer converts to the
game's world (x east, y up, z south) exactly as `RendererLivingEntity` does.
A mob at yaw 0 faces south, so its front is the +z side.

## Traps that bit a real asset

From a live eyeball-tree build (a log, a stripped log, planks, leaves, a
sapling). Each one cost a round trip; none of them is object-specific.

**1. `pattern` sampling copies saturated source pixels verbatim.** On a whole
block face it pulled 50 of 256 pixels back as the source's own olive-browns,
which read as dirt on a red tree. `pattern` is for keeping *grain*, and it
keeps roughly 40% of the source colour to do it. When the target material is
not the source material, either use `value` (brightness only) or write the
value bands straight into the plan's `pixel_map` and keep the reference only
as evidence. Recorded fix: the bands went into `pixel_map`, the reference
stayed attached so `texture_audit.json` could still measure that the source's
value rhythm survived.

**2. A repeated pale accent along one line reads as a band.** Several
`blood_pale` pixels adjacent on a 1px trickle turned it salmon pink. Along any
one run of accent pixels, allow a single palest value; let the rest take the
mid tone.

**3. One plan is one texture. A block is often two.** A log needs an end-grain
file *and* a side file. Building it one plan at a time can only produce
`cube_all`, which is how a log ended up with its side texture on all six
faces. Declare the faces instead:

```json
{ "namespace": "eyeballtree",
  "textures": { "log": "out/log/sprite.png", "log_top": "out/log_top/sprite.png" },
  "blocks": [ { "name": "eyeball_log", "model": "cube_column",
                "faces": { "end": "log_top", "side": "log" }, "item": true } ] }
```

```bash
M pack --manifest pack.json --out pack/
```

It writes the pack, a `FACE_MAP.txt` saying which texture lands on which face,
and a preview that really separates them. A `cube_column` whose `end` equals
its `side` is reported as a warning, because that is exactly what one plan per
texture produces by accident.

**4. The single-plan isometric preview is always `cube_all`.** It does not know
a block has faces. Do not diagnose a face problem from it — read
`FACE_MAP.txt` or the model JSON. A user once reported "your stripped log has
the top face on all six sides"; the pack was correct and the preview was the
liar.

**5. Sibling assets share a cut face.** A stripped log's top is the *same cut*
as the barked log's top with the outer ring planed off — not a different
source tile. Measure it: the live pair came out 77% identical with every
difference in the outermost ring and zero inside.

**6. Check the family, not just the frames.** `measure` covers frame-to-frame
agreement. Also confirm, per asset:

- **palette adherence** — no pixel outside the family palette (count them; zero
  is the target, and a stray source colour shows up here);
- **material honesty** — no leaf pigment in wood, no bark in leaves, the
  sapling's trunk drawn from the log's bark ramp;
- **an accent budget** — decide how many pixels may carry the signature colour
  (blood, glow) and compare every asset against it;
- **hue spread** across the wood family — one narrow band, not several.

## Entities: one description, three artefacts, two branches

Whatever the mob is, an entity asset is the same three things:

| artefact | what it is | who makes it |
|---|---|---|
| `model.json` | parts, pivots, rotations, boxes with 3D placement | `mc-art model` reads it from bytecode, or you write it |
| `layout.json` | which rectangle of the atlas is which face | falls out of the model |
| the audit | every opaque texel lands inside a rectangle | `mc-art entity --atlas`, and `mc-art ingame` |

Making the mod is Branch B, and it is not the fallback: when the mob is
yours, the boxes are the design decision and the layout, the render spec and
the Java all derive from them.

So there are exactly two branches and only the first line differs.

**Branch A — the model exists.** A vanilla mob, a reskin of one, or a mod that
shipped its model. Its texture offsets are the game's, not yours to choose:

```bash
JAR=game.jar
$M model --jar "$JAR" --texture textures/entity/sheep/sheep.png --emit-spec work/model.json
$M ingame --model work/model.json --source "$JAR" --out work/atlas_view.png   # look at the net
# paint into the rectangles the game already reads, then close the loop:
$M entity --spec work/model.json --out work --atlas work/atlas.png
$M ingame --model work/model.json --texture work/atlas.png --out work/view.png
```

Note that there is no entity model file to read. A 1.12 asset root has 878 block
and 717 item JSONs under `models/` and *zero* entity ones: the geometry is Java,
and the jar is obfuscated, so `ModelSheep1` is a class called `bqp` that nothing
in the archive names. `mc-art model` walks texture string → renderer → model →
constructor bytecode and recovers the boxes, the pivots and the pose from it. It
needs `javap`, which ships with any JDK.

**Branch B — the model does not exist.** This is the normal case when you are
*making* the mod: the mob is yours, so the boxes are the design decision and
everything else derives from them.

```bash
cat > work/model.json <<'JSON'
{ "name": "blood_slime", "tex": [64, 32], "parts": [
    { "name": "gel",  "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-4,16,-4], "w": 8, "h": 8, "d": 8 } ] },
    { "name": "core", "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-3,17,-3], "w": 6, "h": 6, "d": 6 } ] } ] }
JSON

# layout.json to paint against, model.json to render, and the mod's Java
$M entity --spec work/model.json --out work --java work/src --class-name ModelBloodSlime

# paint a 64x32 atlas following work/layout.json, then close both loops:
$M entity --spec work/model.json --out work --atlas work/atlas.png    # PASS / FAIL
$M ingame --model work/model.json --texture work/atlas.png --out work/view.png

# and if anyone hand-edits the Java, read it back and diff:
$M model --java work/src/ModelBloodSlime.java --emit-spec work/back.json
```

The generated class is not scaffolding to throw away. Its `addBox` calls *are*
the rectangles FENClayout.json` handed you, emitted from the same spec, so the
atlas and the mod cannot drift — `--java` reads it back into a spec, and
`tests/test_modjava.py` asserts the round trip is the identity. Change the
geometry and regenerate; then the diff is a diff, not a debugging session.

`at` is `addBox`'s offset, `pivot` is `SetRotationPoint` and `inflate` is the
delta. `mc-art model` recovers all of that from bytecode except the pose:
`rot` is per-frame, set in `setRotationAngles`, and the shipped controls carry
the one constant that matters (a quadruped torso is `body.rotateAngleX = 90`,
which is why forgetting it lays the cow out flat).

Mind the axes, because that is where the fourth bug lived: model space is
**y down, z backward**, one unit is 1/16 block, and the renderer converts to the
game's world (x east, y up, z south) exactly as `RendererLivingEntity` does.
A mob at yaw 0 faces south, so its front is the +z side.

## Traps that bit a real asset

From a live eyeball-tree build (a log, a stripped log, planks, leaves, a
sapling). Each one cost a round trip; none of them is object-specific.

**1. `pattern` sampling copies saturated source pixels verbatim.** On a whole
block face it pulled 50 of 256 pixels back as the source's own olive-browns,
which read as dirt on a red tree. `pattern` is for keeping *grain*, and it
keeps roughly 40% of the source colour to do it. When the target material is
not the source material, either use `value` (brightness only) or write the
value bands straight into the plan's `pixel_map` and keep the reference only
as evidence. Recorded fix: the bands went into `pixel_map`, the reference
stayed attached so `texture_audit.json` could still measure that the source's
value rhythm survived.

**2. A repeated pale accent along one line reads as a band.** Several
`blood_pale` pixels adjacent on a 1px trickle turned it salmon pink. Along any
one run of accent pixels, allow a single palest value; let the rest take the
mid tone.

**3. One plan is one texture. A block is often two.** A log needs an end-grain
file *and* a side file. Building it one plan at a time can only produce
`cube_all`, which is how a log ended up with its side texture on all six
faces. Declare the faces instead:

```json
{ "namespace": "eyeballtree",
  "textures": { "log": "out/log/sprite.png", "log_top": "out/log_top/sprite.png" },
  "blocks": [ { "name": "eyeball_log", "model": "cube_column",
                "faces": { "end": "log_top", "side": "log" }, "item": true } ] }
```

```bash
M pack --manifest pack.json --out pack/
```

It writes the pack, a `FACE_MAP.txt` saying which texture lands on which face,
and a preview that really separates them. A `cube_column` whose `end` equals
its `side` is reported as a warning, because that is exactly what one plan per
texture produces by accident.

**4. The single-plan isometric preview is always `cube_all`.** It does not know
a block has faces. Do not diagnose a face problem from it — read
`FACE_MAP.txt` or the model JSON. A user once reported "your stripped log has
the top face on all six sides"; the pack was correct and the preview was the
liar.

**5. Sibling assets share a cut face.** A stripped log's top is the *same cut*
as the barked log's top with the outer ring planed off — not a different
source tile. Measure it: the live pair came out 77% identical with every
difference in the outermost ring and zero inside.

**6. Check the family, not just the frames.** `measure` covers frame-to-frame
agreement. Also confirm, per asset:

- **palette adherence** — no pixel outside the family palette (count them; zero
  is the target, and a stray source colour shows up here);
- **material honesty** — no leaf pigment in wood, no bark in leaves, the
  sapling's trunk drawn from the log's bark ramp;
- **an accent budget** — decide how many pixels may carry the signature colour
  (blood, glow) and compare every asset against it;
- **hue spread** across the wood family — one narrow band, not several.

## Entities: one description, three artefacts, two branches

Whatever the mob is, an entity asset is the same three things:

| artefact | what it is | who makes it |
|---|---|---|
| `model.json` | parts, pivots, rotations, boxes with 3D placement | `mc-art model` reads it from bytecode, or you write it |
| `layout.json` | which rectangle of the atlas is which face | falls out of the model |
| the audit | every opaque texel lands inside a rectangle | `mc-art entity --atlas`, and `mc-art ingame` |

So there are exactly two branches and only the first line differs.

**Branch A — the model exists.** A vanilla mob, a reskin of one, or a mod that
shipped its model. Its texture offsets are the game's, not yours to choose:

```bash
JAR=game.jar
$M model --jar "$JAR" --texture textures/entity/sheep/sheep.png --emit-spec work/model.json
$M ingame --model work/model.json --source "$JAR" --out work/atlas_view.png   # look at the net
# paint into the rectangles the game already reads, then close the loop:
$M entity --spec work/model.json --out work --atlas work/atlas.png
$M ingame --model work/model.json --texture work/atlas.png --out work/view.png
```

Note that there is no entity model file to read. A 1.12 asset root has 878 block
and 717 item JSONs under `models/` and *zero* entity ones: the geometry is Java,
and the jar is obfuscated, so `ModelSheep1` is a class called `bqp` that nothing
in the archive names. `mc-art model` walks texture string → renderer → model →
constructor bytecode and recovers the boxes, the pivots and the pose from it. It
needs `javap`, which ships with any JDK.

**Branch B — the model does not exist.** Then the boxes are the design decision
and everything else derives from them:

```bash
cat > work/model.json <<'JSON'
{ "name": "blood_slime", "tex": [64, 32], "parts": [
    { "name": "gel",  "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-4,16,-4], "w": 8, "h": 8, "d": 8 } ] },
    { "name": "core", "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-3,17,-3], "w": 6, "h": 6, "d": 6 } ] } ] }
JSON
$M entity --spec work/model.json --out work    # assigns u/v, writes work/layout.json
# paint a 64x32 atlas following work/layout.json, then:
$M entity --spec work/model.json --out work --atlas work/atlas.png
$M ingame --model work/model.json --texture work/atlas.png --out work/view.png
```

`at` is `addBox`'s offset, `pivot` is `setRotationPoint`, `inflate` is the delta,
and `rot` is in **degrees** — model space is y down, z backward, one unit is 1/16
block. Leave `u`/`v` out and the packer assigns them; state them and they are kept
verbatim, which is exactly what Branch A relies on, and why the two branches meet
at the same `mc-art ingame` call.

**Both branches end by looking.** That is the only step that catches a part buried
inside another part, a layer that shows through, or a torso standing on end. See
"Before you hand it over", above.

Three traps that are specific to this work:

- **A wool overlay is not the skin model inflated.** `ModelSheep1` (skin,
  `sheep.png`) has a `6x6x8` head and `4x12x4` legs; `ModelSheep2` (wool,
  `sheep_fur.png`) has a `6x6x6` head and `4x6x4` legs. Same texture offsets,
  different nets: `28x14` against `24x12` for the head, `16x16` against `16x10`
  for the legs. Lay the fur out on the skin's numbers and the head draws four
  pixels too wide while the legs run six pixels proud of their frame.
- **Vanilla leaves dead pixels, and that is not your bug.** `sheep_fur.png` paints
  a full 12-tall leg where `ModelSheep2` reads only the top six rows, and both
  sheep atlases carry 12 stray pixels beside the body. So the audit reports both
  directions: **stray** (painted, sampled by nothing) is the one that means a part
  is missing or misplaced; **empty** (a rectangle nobody painted) is usually a face
  the player can never see, which is why the artists left it blank.
- **A hand-typed spec fails silently.** The one written while building this lost
  two of a sheep's four legs and still rendered a plausible animal. Generate it
  (`--emit-spec`) or derive it (`mc-art entity`), and read the part inventory
  `ingame` prints before it draws.

## Art literacy: where the eye is allowed to go

Learned the expensive way, over three rounds of "make the stone quieter".

**1. Accent is a budget, granted by role.** A live flesh-biome set put
eye-catching pustules and bright blood lines on *ordinary stone*; the user's
reaction was that mining a backpack of it would be exhausting. The rule that
settled it:

| block | grain | colour | accent pixels |
|---|---|---|---|
| ordinary stone | 1px fine | dark, desaturated, in family | **0** |
| soil / dirt | coarse + debris | brighter, more saturated | a few |
| ore | inherits the stone base | — | the bright cluster, and the only one |

Assert it rather than intend it: count accent pixels per sprite and check the
plain blocks are at zero. That check is what keeps "quiet" from drifting back.

**2. A block's hue belongs to its biome, not to its source texture.** Grey
stone among red blocks reads as a hole in the terrain, however good the vanilla
grey was. Re-derive the family from the biome and let the source supply only
structure (grain, mottling).

**3. Separate by structure, not only by hue.** Two blocks told apart by
shade alone will be confused. Declare the axis you are separating on —
particle size, saturation, value — and keep it consistent: stone got 1px fine
grain and ended darker and less saturated than the soil, so the two cannot be
mistaken even in a screenshot.

**4. When a check fails, add content — do not loosen the check.** Three assets
failed a "every block bleeds somewhere" rule. The fix that shipped added the
blood to those three, not an exemption to the rule.

**5. Ask for the render before believing the plan.** Two separate rounds ended
with the user saying the result did not look like what the numbers said. The
sprite is the evidence, and a preview that flatters it is worse than none.

## Invariants

1. **Look at the source art before designing.** Text summaries lose everything
   that makes a sprite readable.
2. **One family member is the shape authority.** `bow_standby` ↔
   `bow:bow_standby`; `crystal_bow_pulling_1` suffix-matches `bow_pulling_1`.
   `evidence` marks it and demotes the siblings to context. A standby bow once
   shipped the half-drawn frame's silhouette because all four arrived as
   `roles=shape` and the first one listed won.
3. **Family contracts are per reference group.** Frames of one object share a
   shape relation, a colour ramp and a paint plan; members from *different*
   groups (helmet vs chestplate) must not.
4. **Your plan is the record.** Nothing is inferred back from pixels; whatever
   produced a sprite is in the plan file next to it.

## Legacy

The predecessor pipeline, which drove its own model calls for routing,
planning, drawing and review, is archived at
https://github.com/GMH13552/mc-art-pipeline. Consult it as a baseline and as a
source of plan examples, not as a dependency: its vision "blind reviewer" read
a 16x16 bow as *trident* and a clock as *music disc*, its repair loops burned
about ten calls per member chasing that noise, and two family members were lost
to a reasoning budget that exhausted the completion before any output.
