---
name: mc-art
description: Use when generating or editing Minecraft pixel art (item/block/entity textures, resource packs), matching a mod's or vanilla's existing style, or auditing generated sprites frame-by-frame. A deterministic engine that scans asset roots, turns images into evidence, rasterises an authored plan and measures the result — every design decision is the caller's.
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
