# mc-art

A skill that lets a **text-only or multimodal model generate Minecraft art
assets**.

There is no model baked in. The engine scans asset roots, turns pixels into
evidence, rasterises a plan the model writes, and measures the result.
Everything needing judgement — what the asset should look like, which existing
art anchors it, whether the render is any good — belongs to the model, which
iterates in about a second per attempt with no API cost.

## Install

Drop this directory (or symlink it) into a skills root:

```bash
ln -s "$PWD" ~/.dsh/skills/mc-art
```

## Use

```bash
M=bin/mc-art
JAR=<vanilla jar | mod jar | directory containing assets/>

$M list-groups --source "$JAR" --filter bow          # what logical names exist
$M list-groups --source "$JAR" --extract minecraft:item/bow --to /tmp/refs
$M evidence    --source "$JAR" --name bow --member bow_standby
$M render      --plan my.plan.json --out outputs/mine
$M why-reference --plan my.plan.json                 # which reference, and which step picked it
$M audit       outputs/mine/sprite.png --accent-color '#F2C070' --accent-budget 36
$M audit       outputs/*/sprite.png --family         # one material axis, one accent hue?
$M block       --spec desk.json --out pack/ --render look/desk.png   # block entity, per-face UVs
$M measure     a.png b.png --baseline src.png
```

`evidence` materialises the reference textures into `references/` (gitignored:
they are Mojang's, and content-addressed, so the same jar reproduces the same
paths).

## The art-quality gates

Four of the things people ask for by name are declarations now, and each has a
check that can fail:

| what was asked for | the keys | the check |
|---|---|---|
| "渐变，不是等值散点" | `shade_mode: gradient`, `band_maximum_isolated`, `band_maximum_step` | the share of opaque pixels isolated from every neighbour, and the mean luma step between adjacent pixels |
| "最多 N 个强调像素；普通方块是 0" | `accent_budget`, `accent_colors` | accent pixels counted on the delivered PNG |
| "不能是这么难看的点点" | `accent_min_cluster`, `accent_cleanup` | cluster sizes; the declared repair repaints undersized ones |
| "橙色和蓝色的边缘要拖突兀" | `accent_edge_max`, `audit --family` | the luma step where the accent meets its material, and the accent-hue arc across the family |
| "参考选错了" | — | `why-reference` / `reference_selection.json` name the chosen source, every rejected candidate, and the step that broke when nothing was usable |

`examples/example_family/` is a whole worked family — one stone, its deepslate
variant, that stone's ore, a raw lump and an ingot — with `build.py` (pull the
vanilla references, render, re-measure, write a contact sheet) and
`fault_demo.py`, which injects the faults people reported and asserts that each
gate turns red. `examples/example_block_entity/` builds a desk with a sheet of
paper on it and renders the correct model beside the two ways it comes out as
stretched planks.

## What is here

| path | |
|---|---|
| `SKILL.md` | the procedure the model follows, including which decisions are the model's and which are the engine's |
| `bin/mc-art` | the whole tool surface: scan / evidence / render / pack / measure / audit / why-reference / block / model / entity / ingame |
| `mc_art/` | the engine — 22 modules, Pillow + stdlib, never touches a network |
| `examples/` | working plans, including a rendered family and a block entity with their own build and fault scripts |
| `layouts/` | worked examples. `mc-art model --emit-spec` reads any mob from any jar, so these are references to compare against rather than tables to reach for |
| `scripts/` | magnify / review_family / continuity |
| `tests/` | 182 tests over the engine |

Three modules are worth calling out. `vanilla_model` reads an entity model out of
compiled, usually obfuscated bytecode, because a 1.12 asset root has no entity
model files at all. `ingame` renders a block or entity model the way the game
does — same axes, same lighting, same UV corner order — so a delivery can be
looked at in the game's own view before it ships, with the vanilla cow, sheep and
slime shipped as controls that have to come out right first. `entity` and `modjava` are the other half: when the mob is one you are making, they turn a single box description into the atlas rectangles and the mod Java model class, so the texture and the code cannot drift.

Two more exist for the art itself: `style` measures a delivered PNG — value
bands, accent spend, accent-to-base step, and the hue/value/accent axes of a
whole family — and `blockmodel` writes and audits multi-box block models, where
every face must sample a same-shaped rectangle of a texture declared for it.

## Why there is no model inside

The predecessor of this skill (`mc-art-pipeline`) drove its own API calls for
routing, planning, drawing and review. Measured on that path:

- ~10 model calls per asset member at 45–90 s each, dominated by thinking
  tokens (11k–33k per call, 2.7x spread on identical input);
- a vision "blind reviewer" that read a 16x16 bow as *trident* and a clock as
  *music disc*, then drove repair loops chasing that noise;
- two whole family members lost to a completion budget exhausted by reasoning
  before any output, and one to a truncated JSON body.

Replacing the model with the calling agent removes all of it, and lets the
agent look at the actual pixels instead of a summary of them.
