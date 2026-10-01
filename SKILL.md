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
mc_art/             the engine (22 modules, Pillow + stdlib)
examples/           working plans to start from; example_family/ is a whole
                    family plus the script that builds and re-measures it
layouts/            shipped vanilla UV layouts
scripts/            magnify / review_family / continuity
tests/              182 tests over the engine
```

Run them with `python -m pytest tests -q`. On a Windows box with a stale
`pytest-of-<user>` directory under `%TEMP%` that your account cannot read, pytest
cannot create its default temp root and reports a page of `PermissionError` that
has nothing to do with this repository — pass a temp root **outside this
directory**, e.g. `--basetemp="$env:TEMP\mc-art-pytest"`. Scratch left inside the
tree gets copied by anything that vendors this skill wholesale, absolute paths
and all.

## Who decides: you, or the engine

The single most expensive mistake this skill has made is letting a script guess
at something only the eye can answer, and then blaming the script when the art
came out wrong. The split below is a contract, not a preference.

| decision | owner | why |
|---|---|---|
| what the asset *is*; which object it must read as | **you** | no statistic knows that a lectern is not a table |
| which reference to learn from, and in what role | **you** (engine scores the candidates) | the engine can rank them; only you can say "the deep variant, not the shallow one" |
| silhouette: keep, edit locally, or redraw | **you** (`shape_edit_mode`) | the engine only enforces what you declared |
| palette hue, ramp order, accent hue | **you** | it is the answer to the brief |
| composition, marks, `pixel_map` rows | **you** | placement is art |
| whether the render is any good | **you**, by reading the PNG | numbers lie in both directions |
| where the accent pixels ended up; how many | **engine** (`audit`, `style_report.json`) | counting is not judgement |
| whether the accent budget you declared was respected | **engine** (`validate_style`) | a budget you assert beats a budget you intend |
| whether the family shares one hue and one accent | **engine** (`audit --family`) | this is arithmetic on delivered pixels |
| which reference the renderer actually loaded | **engine** (`why-reference`, `reference_selection.json`) | the answer must be the same computation that chose |
| alpha conformance to a locked contour | **engine** | exactness is not a matter of taste |

If you catch yourself writing a script to decide a *taste* question, stop: that
is you making the decision through a script, and it will not generalise past
the asset you were looking at. Write the decision into the plan and let the
engine execute it.

## The loop

**Run every command from the skill's own root directory.** The engine imports
`mc_art` from there, so the working directory is part of the invocation.

Pick the entry point for your platform — all three find Python the same way, and
all three are the same engine:

| platform | entry point |
|---|---|
| any (recommended) | `M="<interpreter> -m mc_art"` |
| POSIX / WSL / Git Bash | `M=bin/mc-art` |
| Windows cmd | `M=bin\mc-art.cmd` |
| Windows PowerShell | `M=bin\mc-art.ps1` |

The first form is the portable one and is what the examples below use: find the
interpreter that actually runs, then ask it for the module. On Windows that is
usually `python`; **`python3` is often a 0-byte Microsoft Store stub that exists,
is found on PATH, and exits 9009 without running anything**, which is why nothing
here names it.

```bash
M="python -m mc_art"          # Windows + DSH's bundled Python, and most POSIX too
# M="bin/mc-art"              # POSIX if you prefer the wrapper
# M="bin\mc-art.cmd"          # Windows cmd
JAR="<path to a vanilla jar, a mod jar, or a directory with assets/>"
OUT="<a scratch output directory you can write to>"

# 1. SCAN — what logical names exist (one name = all of its textures)
$M list-groups --source "$JAR" --filter bow --limit 5

# 2. LOOK — pull the real PNGs and read them with the image reader.
#    Never design from a text summary alone.
$M list-groups --source "$JAR" --extract minecraft:item/bow --to "$OUT/refs"

# 3. EVIDENCE — when you need pixel precision: the literal per-pixel text,
#    and which frame of a family answers this request
$M evidence --source "$JAR" --name bow --member bow_standby

# 4. AUTHOR a plan (your decisions), then RASTERISE it
$M render --plan my.plan.json --out outputs/mine

# 5. LOOK at outputs/mine/sprite.png — then go back to 4
#
# 5b. MEASURE what you delivered (not what you intended):
#    $M audit outputs/mine/sprite.png --accent-color '#F2C070' --accent-budget 36
#    $M audit outputs/*/sprite.png --family --accent-color '#F2C070'   # one family?
#    $M why-reference --plan my.plan.json                              # why THAT source?
#
# 6. For an asset whose faces differ (a log, a machine, a plant), assemble it:
#    $M pack --manifest pack.json --out pack/     see "Traps" #3
#
# 6b. For a BLOCK ENTITY (a desk with a sheet on it, a lectern, an altar) the
#     block is several boxes with per-face UVs, not a recoloured plank:
#    $M block --spec desk.json --out pack/ --render look/desk.png --view front34
#    A face with no `uv` on a partial box samples the WHOLE texture; the audit
#    refuses that before it ever renders.
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
#      $M ingame --selftest --out "$OUT/look"      # asserts the renderer itself
```

Step 4 takes about a second and needs no credentials. Iterate as long as the
art needs: palette, sampling, mask shapes, highlight placement.

### First question, before any plan: does this thing already exist in the game?

**If it has a vanilla counterpart, use that counterpart's shape as the authority**
(`shape_edit_mode: appearance_only` against it) and make the material yours. An
ore block has `minecraft:block/iron_ore`; raw ore has `minecraft:item/raw_iron`;
an ingot has `minecraft:item/iron_ingot`. Inventing a contour for an object the
game already draws is how a delivery ends up with a raw ore that "doesn't
reference raw iron at all" -- and the reference was sitting on disk the whole
time.

**If the contour really is new, say why.** A render with no usable reference and
a `shape_edit_mode` other than `appearance_only` fails the `reference` stage
unless `descriptor.reference_waiver` states the reason. It is one sentence and it
turns a silent decision into a recorded one.

The engine holds you to that, two ways it can tell you apart:

* **`reference` stage** -- no reference was offered at all. Always a warning; an
  error when the shape is not a plain recolour.
* **`reference_pool` stage** -- a same-class reference was *available on your
  reference root* and the plan did not attach it. Pass
  `mc-art render --reference-pool <dir>` (or, from a script, set
  `available_references` on the plan) and the engine compares the plan's
  `references` against everything the root holds, scoring both sides by the
  content words their names share with the asset. If the best unattached
  candidate beats the best attached one, that is an error unless waived.

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

## Ramp, line and family: the declarations that turn a complaint into a check

Four things people keep having to point at by hand. Each one has a key you can
write and a number the engine reports back, so "it looks like dots" becomes a
verdict instead of an argument.

### 1. A ramp, not equal-value scatter

```json
"parts": {"face": {"colors": ["deep_1","deep_2","deep_3","deep_4","deep_5"],
                   "shade_mode": "gradient",
                   "shade_axis": "top_left_to_bottom_right"}},
"band_maximum_isolated": 0.05
```

- `shade_mode: "bands"` (default) snaps every pixel to one authored swatch —
  that is what gives vanilla its pixel-art value steps.
- `shade_mode: "gradient"` interpolates continuously between them. Use it for a
  rounded solid (a lump, a gem, a metal body); keep `bands` for a tiled surface.
- **Contrast collapses without a region.** `pattern` sampling of a same-size
  reference normalises the source's value range *inside a declared UV region*.
  With no region it uses absolute source luma, and a grey-stone tile ends up
  using two of your five swatches (measured on a real asset: luma std-dev 5.6
  against deepslate's 21.1). Declare one region covering the face —
  `"uv_regions": [{"id":"face_all","part_id":"face","bbox":[0,0,16,16],"face":"all"}]`
  — and the whole ramp comes back.
- `band_maximum_isolated` is the gate: the share of opaque pixels allowed to
  differ from *every* in-mask neighbour by more than 24 luma. A smooth ramp
  scores 0; a flat fill with a few bright pixels scores their share.

### 2. Accent is a budget, and the engine counts it

| key | what it says |
|---|---|
| `accent_colors` | which swatches *are* the accent (hex or palette tokens). Omit and the engine infers it: saturated pixels outside the sprite's own hue |
| `accent_budget` | the most accent pixels this sprite may spend. **A plain block declares `0`** |
| `accent_min_cluster` | the smallest accent deposit accepted, in pixels. A gate: it reports, it does not fix |
| `accent_cleanup` | `true` also *repairs*: undersized clusters are repainted with the nearest base pixel before the PNG is written. Off by default — silently rewriting a delivered pixel is not the engine's call |
| `accent_edge_max` | the most luma the accent may jump by where it meets the material. This is "橙色和蓝色的边缘要拖突兀有多突兀", as a number |
| `band_maximum_isolated` | see above |

```bash
$M audit outputs/example_ore/sprite.png \
    --accent-color '#F2C070' --accent-color '#8E5C1C' --accent-budget 36 --min-cluster 3
#   accent  -> 32 pixel(s) (declared) at hue 34.4 deg, mean value 156.0, in 4 cluster(s)  smallest=8  below_min=0
#   edge    -> mean=12.19 p90=26.72 (limit None)
```

A budget of zero is the whole point of the plain-block rule: it cannot be
satisfied by taste, only by having no accent pixels at all.

### 2b. Structure: does it read as drawn, or stamped?

The gates above are *violation* gates -- did a limit get broken. They cannot see
the delivery a user rejected twice: four identical 8-pixel crosses on a grid
breach no budget, no minimum cluster size and no edge limit. They are legal
deposits, and the face still reads as a rubber stamp. Three more keys:

| key | what it refuses |
|---|---|
| `accent_motif_repeat_max` | the same deposit **shape** (outline, position and shade removed) appearing more than this many times. Default 2: a deliberate pair is fine, a set of four is a stamp |
| `accent_layout_min_size_cv` + `accent_layout_min_spacing_cv` | every deposit the same size **and** the same distance apart. Both spreads must be below their minimum before it fires, so an ore whose flecks happen to be similar in size is not punished |
| `accent_ramp_min_pixels` / `_min_levels` / `_max_dominant_share` / `_min_monotone` | a deposit large enough to have an interior that spends itself on one flat shade. Every threshold is measured against vanilla: iron_ore's own specks score 4/4/3 levels, dominant shares 0.33-0.50, monotone 0.86-1.0 |

They are calibrated on real art, not invented: `examples/example_family/fault_demo.py`
keeps the **exact pixel maps of the rejected delivery** as fixtures
(`--only motif-repeat`, `--only layout-grid`, `--only ramp-flat`), so each rule
can be watched firing on the thing it was written for. `layout-grid` uses four
*different* stamps on an even grid, which is what proves the layout rule is not
the motif rule wearing a hat.

### 3. One family is one material axis *and* one accent axis

```bash
$M audit outputs/example_{stone,deepslate,ore,raw_ore,ingot}/sprite.png \
    --family --accent-color '#F0BE6E'
# family: hue_span=6.4 deg  value_span=41.46  chroma_span=13.65  accent_hue_span=0.1 deg (3 member(s) carry an accent)  -> consistent
```

- The **material axes** (hue span, value span, chroma span) are measured with
  accent pixels *removed*, so a warm highlight line does not count as drift.
- The **accent axis** is measured across the members that carry an accent. One
  member with an orange seam and another with a cyan seam is the real "你能看出
  两个本来是一个东西的吗" failure, and a base-only test cannot see it.
- Thresholds are yours: `--max-hue-span`, `--max-value-span`,
  `--max-accent-hue-span`. The defaults (26° / 56 / 14°) pass vanilla stone →
  deepslate, which is the bar that matters.
- **A palette-histogram overlap is not the bar.** Two unrelated materials share
  a great many quantised colours; `family_consistency` can pass a pair this test
  fails. Use `family_consistency` for frame-to-frame drift within one object and
  `audit --family` for "are these one material".

### 4. "Why did it use *that* reference?"

```bash
$M why-reference --plan my.plan.json
# CHOSEN -> deepslate [shape,material,pixel_style] mode=exact score=300.0
# WHY    -> highest score (alpha overlap 1.0 x 100 + role weight 3); 1 other candidate(s) were scored lower
# CANDIDATES ->
#   deepslate      shape,material,pixel_style  score=300.0    eligible (alpha_overlap=1.0)
#   stone          shape                       score=300.0    eligible (alpha_overlap=1.0)
```

The renderer writes the same table to `reference_selection.json` on every run,
including the candidates it rejected and the rule that rejected each one. The
report is not a second opinion computed beside the renderer — it *is* the
selection computation, so "it referenced the shallow stone instead of the deep
one" is answerable from the output directory rather than by re-running with
extra logging.

### 5. A block entity is boxes and UVs, not a recoloured plank

A desk with a sheet of paper on it is not a plank texture with a lighter
rectangle. It is several boxes, each face sampling its own rectangle of a
texture painted for that face:

```json
{"name": "example_desk", "namespace": "examplepack",
 "textures": {"wood": "example_planks.png", "paper": "example_paper.png"},
 "elements": [
   {"id": "top",   "from": [0,13,0], "to": [16,16,16], "faces": {"up": {"texture":"wood","uv":[0,0,16,16]}, "...": {}}},
   {"id": "sheet", "from": [2,16,3], "to": [14,16,13], "faces": {"up": {"texture":"paper","uv":[0,0,12,10]}}}
 ]}
```

```bash
$M block --spec desk.json --out pack/ --render look/desk.png --view front34 --view side
```

The audit refuses, per face: a **missing `uv` on a partial box** (that face would
inherit the whole texture, which is exactly how a sheet of paper comes out as
stretched planks), a `uv` **outside** its texture, and a `uv` whose rectangle is
**not the shape of the face it is mapped onto**. One block unit is one texel at
16x16, so "is this UV stretched?" has an exact answer with no object knowledge.
Then `--render` draws the emitted model through the game's own camera, because
the numbers passing is not the same as the desk looking like a desk.

## After every render: look at the image

Never hand back a render you have not looked at. The numbers have been wrong in
both directions; the sprite is the evidence.

```bash
S=scripts
# "$PY" is the interpreter that actually runs; the wrapper scripts set it for you.
$PY "$S/magnify.py" '{"out":"'"$OUT"'/check.png","cols":4,"entries":[
  ["SRC","'"$OUT"'/refs/bow_standby.png"], ["MINE","outputs/mine/sprite.png"]]}'
$M measure mine_a.png mine_b.png mine_c.png --baseline "$OUT/refs/bow_standby.png"
```

Read `"$OUT/check.png"` back with the image reader — never describe it from the
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

## The last look: put it beside the vanilla asset and answer three questions

**This step is mandatory, not optional, and it is not the same step as the one
above.** Do it after the render is finished and after every gate is green. A
green gate is the *start* of this step, never a substitute for it.

1. Magnify `sprite.png` **next to the reference the plan actually attached**, in
   one image, and read that image back. For a family, also put the whole contact
   sheet on screen. `scripts/magnify.py` takes both as entries:

   ```bash
   $PY scripts/magnify.py '{"out":"'"$OUT"'/last_look.png","cols":2,"entries":[
     ["VANILLA","refs/iron_ore.png"], ["MINE","outputs/ore/sprite.png"]]}'
   ```

2. **Answer these three questions in words, in the handover note.** Not in your
   head, not as "looks fine" — the actual sentences:

   - **Can you tell what it is?** An ore that reads as ore, an ingot that reads
     as an ingot, without the filename.
   - **Is it ugly?** Judge it as something a player will stare at several hundred
     times, not as something that passed a check.
   - **Beside the vanilla asset, is it from the same game?** Not "does it
     resemble that one reference" — does it look like the *same artist's* work.
     Wrong lighting model, wrong saturation, wrong silhouette language, wrong
     level of detail: each of these makes it a different game.

3. **Any "no" means go back to the plan.** Do not hand it over. Fix the plan,
   re-render, look again.

4. **Say this explicitly in the handover:** `audit` green and `style_report`
   green are **not** the answers to these three questions. They answer "did a
   limit get broken", which is a different question from "is it good". The
   record is unambiguous: a delivery passed every gate, with the budget, the
   hue span and the band metrics all inside their declared limits, and the user
   rejected it three times running — the ore specks were a stamp, the ingot was
   a pasted band, and the palette was two palettes. Every number was green.

If you cannot answer question 3 with the reference tile next to the product,
you have not done this step.

### And a green gate is not evidence that the gate looked at your picture

> Green gates do not mean it looks good; **and a gate may never have measured
> what your eye is looking at** — before handing over, scan the rendered image
> again with a criterion **independent of any declaration**. A pixel nobody
> declared passes every declarative check there is.

That is not a hypothetical. An ingot plan carried both a hand-drawn `pixel_map`
band and a derived `accent_from_reference` highlight. The gates measured the five
pixels the renderer declared. The band was seventeen pixels painted on top of
them. Every check was green, the picture was visibly wrong, and two rounds were
spent tuning parameters that could not change the image — because the numbers and
the picture were describing different pixels.

The engine now refuses that:

* **`unaudited_accent`** scans the finished sprite with no knowledge of the
  palette, the swatches or the reference — it only asks whether a pixel's chroma
  stands out from its own local median — and reports anything it finds that the
  audited set does not contain, with locations and colours.
* A plan declaring **both** `pixel_map` and `accent_from_reference` raises a
  warning, because that combination almost always means one of them is a leftover.

Run it before every handover, and if it fires, believe the picture and delete the
leftover — not the check.

## Before you hand it over: look at it in the game's view

An atlas can land on exactly the right rects and the mob can still be wrong: a
layer that shows through, a part buried inside another part, a face nobody ever
sees, a body that reads as a slab. Nothing above catches any of that, because
everything above is flat. So the last look is a render of the model itself.

```bash
$M ingame --selftest --out "$OUT/look"            # assert the renderer, then look
$M ingame --control cow --control both --source game.jar --out "$OUT/look"
$M model --jar game.jar --texture textures/entity/sheep/sheep.png --emit-spec mine.json
$M ingame --model mine.json --source game.jar --view front34 --out "$OUT/look/mine.png"
$M ingame --block anvil_undamaged --source game.jar --out "$OUT/look/anvil.png"
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

## Blocks: orientation and animation

Two parts of a block's data are easy to miss, because a static element list does
not show either of them.

### Orientation is per-block data, not a table you keep

The properties a block has — `axis`, `facing`, `half`, `shape`, `conditional`,
`rotation` — come from **its own blockstate**. Measured over the whole vanilla
set, `facing` appears in 1338 variant keys (1.12.2) and 3335 (1.18.2); `half`
860 / 2374; `shape` 606 / 1966. So "does this block have an orientation" is a
question you answer by reading the file, never a list you maintain.

Three of the rules are **exact**, and a viewer that knows which face was clicked
and where on it can derive them with nothing from the user:

| property | rule | checked against |
|---|---|---|
| `axis` | the axis of the face you clicked | `BlockLog` / `RotatedPillarBlock` is `direction.getAxis()`; `oak_log` declares `axis=z` as `x:90` and `axis=x` as `x:90, y:90` in **both** versions |
| `half` | bottom face → `top`, top face → `bottom`; on a side, `hitY > 0.5` → `top` | vanilla's own `hitY <= 0.5D` test |
| `rotation` | the player's yaw, `floor(yaw * 16 / 360 + 0.5) & 15` | signs, banners, skulls — and it has **no clicked-face equivalent**, so a viewer with no player in the world cannot derive it at all |

And one is **not** derivable. This is the trap:

> **`facing` is computed in per-block Java, and the two families differ by 180
> degrees.** A furnace ends up facing the player
> (`getHorizontalFacing().getOpposite()`); a staircase ends up facing the way the
> player looks (`getHorizontalFacing()`). Nothing in the blockstate, the model, or
> any file on disk says which family a block belongs to — and it has changed
> between versions for individual blocks.

So do not hard-code a list of which block is which; you will be wrong next
version. Take the majority convention (chest, dispenser, dropper, furnace: toward
the player) as the derived default, make the result **visible in the preview**,
and put the other one **one click away**.

**A resource pack cannot introduce a property.** Blockstate variants map
*property combinations* to models; the property itself is declared by the block's
Java class. A pack-only asset with one unrotated variant — `example_log` is
exactly that — has no `axis` to set, which is why it cannot have orientation, and
why `build_pack.py` prints *"blockstate axis variants and worldgen are mod work"*.
Writing `axis` keys into the blockstate before the mod declares the property is
inert at best.

**`uvlock` is set far more often in 1.18 — and what it does is an open question
here.** 610 variant entries set it in 1.12.2, 1888 in 1.18.2, and stairs set it in
*both* versions, so it is not ignorable. But:

- What is **measured**: how often it appears, and that a renderer which only
  rotates the quad corners is not implementing it at all.
- What is **not** settled: which way it turns the UV. "The texture locks to the
  model" and "the texture stays put while the model turns" both fit the numbers —
  and the blockstate data cannot decide it, because `uvlock` is a *baking*
  instruction, not a value you can read back. The count of 90-degree steps a
  rotate-model-only renderer is missing is likewise unverified.

Settle it by **rendering a vanilla case with and without the UV rotation and
looking at both** — the same standard as everywhere else in this skill. It cannot
be settled before variant rotation exists, because without rotation there is
nothing for `uvlock` to act on.

### The 1.12 / 1.18 differences, measured

The **format is the same**: `variants` keyed by `property=value`, `x` / `y`
rotations, `uvlock`, and `multipart`. Over the whole vanilla blockstate set:

| | 1.12.2 | 1.18.2 |
|---|---|---|
| blockstate files | 407 | 900 |
| uses `variants` | 376 | 840 |
| uses `multipart` | 31 (8%) | 60 (7%) |
| has an `""` empty key | **0** | **493** |
| variant entries with `uvlock: true` | 610 | 1888 |
| `.png.mcmeta` (animated) | 26 | 64 |

- **The `""` empty key is the flattening.** From 1.13 every single-state block
  uses it; 1.12.2 never does. A resolver has to read both.
- **`multipart` is a steady 7–8% in both**, so it is not a version problem: a
  resolver that only reads `variants` loses the same share either way (vanilla
  `fire`, fences, stained-glass panes).
- **One encoding change worth knowing:** 1.18.2 points `axis=x` at a *separate*
  model (`oak_log_horizontal`), where 1.12.2 reuses `oak_log` with `x:90, y:90`.
  The derivation is identical; only the data moved.

### Animated block textures (`.mcmeta`)

The format has not changed since 1.5 — `sea_lantern.png.mcmeta` is
**byte-identical** in 1.12.2 and 1.18.2. Only how common it is changed: 26
animated textures against 64.

An animated texture is a **vertical strip**: frame 0 on top, each next frame
below it. `lava_still` is 16x320, `water_still` 16x512.

```jsonc
{ "animation": { "frametime": 5 } }        // 5 ticks per frame = 250 ms
{ "animation": { "frametime": 3,
                 "frames": [{"index": 0, "time": 5}, 2, 1, {"index": 2}] } }
```

Three things that are easy to get wrong:

1. **`frametime` is in ticks (50 ms), not milliseconds.** Read as ms, a 5-tick
   sea lantern plays twelve times too fast.
2. **`frames` is a list, not a count.** It may reorder and repeat entries, and its
   length need not equal the number of rows: `lava_still` declares 38 playback
   steps over a 20-row strip. Keep the order; a bare count throws it away.
3. **A strip is not a sprite, and the two consumers want opposite things.**
   - The **3D view** wants the whole strip *plus* the description, and samples one
     frame by offsetting `v`: `ty = row * width + v * width`.
   - An **icon** is a CSS background or a single `<img>`: hand it a strip and it
     arrives squashed into one square. It must be cropped to frame 0.

   One extractor, two answers — decide which consumer you are serving.

Two mechanical traps. An animated strip is **tall** (16x512), so whatever scratch
canvas it is decoded onto has to grow to fit — otherwise the animation plays a few
frames and then goes transparent **with no error at all**. And the current frame
number has to be part of the "has the picture changed, redraw" guard, or the timer
runs and the image never moves.

## Traps that bit a real asset

From a live example-pack build (a log, a stripped log, planks, leaves, a
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
`example_pale` pixels adjacent on a 1px trickle turned it salmon pink. Along any
one run of accent pixels, allow a single palest value; let the rest take the
mid tone.

**3. One plan is one texture. A block is often two.** A log needs an end-grain
file *and* a side file. Building it one plan at a time can only produce
`cube_all`, which is how a log ended up with its side texture on all six
faces. Declare the faces instead:

```json
{ "namespace": "examplepack",
  "textures": { "log": "out/log/sprite.png", "log_top": "out/log_top/sprite.png" },
  "blocks": [ { "name": "example_log", "model": "cube_column",
                "faces": { "end": "log_top", "side": "log" }, "item": true } ] }
```

```bash
M pack --manifest pack.json --out pack/
```

It writes the pack, a `FACE_MAP.txt` saying which texture lands on which face,
and a preview that really separates them. A `cube_column` whose `end` equals
its `side` is reported as a warning, because that is exactly what one plan per
texture produces by accident.

A log needs a third thing, and it is **not** something this pipeline can give it:
the `axis` variants. `cube_column` gives the geometry an end and a side, but a
block lying on its side is a different *blockstate*, and the property behind it
is declared in Java. See "Blocks: orientation and animation".

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
{ "name": "example_slime", "tex": [64, 32], "parts": [
    { "name": "gel",  "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-4,16,-4], "w": 8, "h": 8, "d": 8 } ] },
    { "name": "core", "pivot": [0,0,0], "rot": {}, "boxes": [
        { "at": [-3,17,-3], "w": 6, "h": 6, "d": 6 } ] } ] }
JSON

# layout.json to paint against, model.json to render, and the mod's Java
$M entity --spec work/model.json --out work --java work/src --class-name ModelExampleSlime

# paint a 64x32 atlas following work/layout.json, then close both loops:
$M entity --spec work/model.json --out work --atlas work/atlas.png    # PASS / FAIL
$M ingame --model work/model.json --texture work/atlas.png --out work/view.png

# and if anyone hand-edits the Java, read it back and diff:
$M model --java work/src/ModelExampleSlime.java --emit-spec work/back.json
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
{ "name": "example_slime", "tex": [64, 32], "parts": [
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

**1. Accent is a budget, granted by role.** A live biome set put
eye-catching pustules and bright blood lines on *ordinary stone*; the user's
reaction was that mining a backpack of it would be exhausting. The rule that
settled it:

| block | grain | colour | accent pixels |
|---|---|---|---|
| ordinary stone | 1px fine | dark, desaturated, in family | **0** |
| soil / dirt | coarse + debris | brighter, more saturated | a few |
| ore | inherits the stone base | — | the bright cluster, and the only one |

Assert it rather than intend it. This is no longer advice: declare
`"accent_budget": 0` on the plain blocks and `"accent_colors"` on the ones that
carry an accent, and `validate_style` fails the render if the budget is
breached. Measured on `examples/example_family`: stone and deepslate **0/0**,
ore **32/36**, raw ore **16/20**, ingot **16/16**. That check is what keeps
"quiet" from drifting back.

Two more, learned from the same rounds and now measurable:

- **The accent has to be one hue across the family.** The complaint was not
  that the ore had specks; it was that the orange and the blue could not be
  recognised as the same object. `audit --family` reports the accent-hue arc
  separately from the material axes, and the example family measures 0.1° across
  its three accented members.
- **Give the accent a rim, not just a core.** Every boundary pixel of a deposit
  should be the *darkest* tone, with the bright core strictly inside. A bright
  core that touches the base material is the harsh edge people describe as
  "突兀", and `accent_edge_max` is the number that catches it: the family's
  deposits measure a p90 luma step of 19–36 against a limit of 60, while the
  same deposit painted flat measures 137.

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
   `bow:bow_standby`; `example_bow_pulling_1` suffix-matches `bow_pulling_1`.
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
