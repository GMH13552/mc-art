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
$M measure     a.png b.png --baseline src.png
```

`evidence` materialises the reference textures into `references/` (gitignored:
they are Mojang's, and content-addressed, so the same jar reproduces the same
paths).

## What is here

| path | |
|---|---|
| `SKILL.md` | the procedure the model follows, and the plan contract |
| `bin/mc-art` | the whole tool surface: scan / evidence / render / pack / measure / model / entity / ingame |
| `mc_art/` | the engine — 20 modules, Pillow + stdlib, never touches a network |
| `examples/` | a working plan whose contour came out pixel-identical to vanilla |
| `layouts/` | shipped vanilla UV layouts, two of them recovered from ModelSheep1/2 bytecode |
| `scripts/` | magnify / review_family / continuity |
| `tests/` | 114 tests over the engine |

Two modules are worth calling out. `vanilla_model` reads an entity model out of
compiled, usually obfuscated bytecode, because a 1.12 asset root has no entity
model files at all. `ingame` renders a block or entity model the way the game
does — same axes, same lighting, same UV corner order — so a delivery can be
looked at in the game's own view before it ships, with the vanilla cow, sheep and
slime shipped as controls that have to come out right first.

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
