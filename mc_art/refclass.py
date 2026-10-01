"""What KIND of thing a reference is -- as data the plan declares and the engine checks.

The gap this closes. A real project shipped four plans in which one asset
(``smoke_mist_stone``, a *shallow* mist stone) attached ``deepslate.png``, and its
own note said "the shallow variant is available in the same reference root and
must NOT be chosen". Its choice contradicted its stated reason, and nothing could
tell:

* the candidate table scored roles, alpha overlap and a name-token overlap, so
  "deep" and "shallow" were just words in a filename;
* ``notes`` were free text, so a plan could attach the exact reference its note
  forbade;
* notes were copied verbatim between plans, so a wrong reason propagated.

So a reference has two declared, derived properties -- and they are separate,
because they answer different questions:

* **class** -- what it is: ``shallow_stone``, ``ore_deposit``, ``iron``? This is
  what a plan declares and what the chosen reference must match.
* **layer** -- which world layer it belongs to: ``shallow``, ``deep``, ``nether``,
  ``end``? ``deepslate_iron_ore`` is an *ore_deposit* in the *deep* layer, and
  flattening the two would make "this ore is from the wrong layer" unexpressible.

**The table is published, not hidden.** It lives here, it is what the report
prints, and ``mc-art refclass`` prints it too -- if the engine's idea of "deep"
differs from yours, you can read it rather than guess.

Order matters: first match wins, so the specific is tested before the general.
"""

from __future__ import annotations

import re
from typing import Iterable

#: Every class a plan may declare. Inventing a name would put the check back to
#: comparing unreadable strings, so unknown declarations are refused.
CLASSES: tuple[str, ...] = (
    "shallow_stone",
    "deep_stone",
    "nether_stone",
    "end_stone",
    "ore_deposit",
    "metal",
    "gem",
    "brick",
    "polished",
    "planks",
    "log",
    "leaves",
    "glass",
    "wool",
    "sand",
    "dirt",
    "unknown",
)

#: Every layer a reference may belong to.
LAYERS: tuple[str, ...] = (
    "shallow",
    "deep",
    "nether",
    "end",
    "metal",
    "gem",
    "wood",
    "glass",
    "wool",
    "soil",
    "unknown",
)

#: The world layer a name announces, before anything else is considered. An ore
#: named `deepslate_iron_ore` is deep; the same ore named `iron_ore` is shallow.
_LAYER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("deep", (r"deepslate", r"\bsculk")),
    ("nether", (r"blackstone", r"basalt", r"netherrack", r"nether_brick", r"soul_soil")),
    ("end", (r"end_stone", r"purpur", r"endstone")),
)

#: class, patterns. First match wins.
_CLASS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Ores before stone: `iron_ore` and `raw_iron` are deposits, not stone, and
    # the layer they sit in is reported separately.
    ("ore_deposit", (r"_ore$", r"^ore_", r"_ore_", r"^raw_", r"_raw$", r"ancient_debris")),
    # Deep before shallow once ores are out of the way.
    ("deep_stone", (r"deepslate", r"sculk", r"tuff")),
    ("nether_stone", (r"blackstone", r"basalt", r"netherrack", r"nether_brick", r"soul_soil")),
    ("end_stone", (r"end_stone", r"purpur", r"endstone")),
    ("metal", (r"iron", r"gold", r"copper", r"netherite", r"ingot", r"nugget", r"chain")),
    ("gem", (r"diamond", r"emerald", r"lapis", r"redstone", r"quartz", r"amethyst", r"coal")),
    ("brick", (r"brick",)),
    (
        "shallow_stone",
        (
            r"^stone$", r"^stone_", r"_stone$", r"cobblestone", r"andesite", r"diorite",
            r"granite", r"calcite", r"dripstone", r"terracotta",
        ),
    ),
    ("polished", (r"polished", r"chiseled", r"smooth", r"cut_")),
    ("planks", (r"planks", r"slab", r"stairs")),
    ("log", (r"_log$", r"^log_", r"_wood$", r"stripped_", r"hyphae")),
    ("leaves", (r"leaves", r"sapling", r"vine")),
    ("glass", (r"glass", r"pane")),
    ("wool", (r"wool", r"carpet", r"_bed$")),
    ("sand", (r"sand",)),
    ("dirt", (r"dirt", r"grass_block", r"podzol", r"mycelium", r"gravel", r"clay")),
)

#: A class's layer when its name announced none -- `oak_planks` is wood, and wood
#: is not a world layer, but it is the axis along which wood variants differ.
_CLASS_LAYER: dict[str, str] = {
    "shallow_stone": "shallow",
    "deep_stone": "deep",
    "nether_stone": "nether",
    "end_stone": "end",
    "ore_deposit": "shallow",
    "metal": "metal",
    "gem": "gem",
    "brick": "shallow",
    "polished": "shallow",
    "planks": "wood",
    "log": "wood",
    "leaves": "wood",
    "glass": "glass",
    "wool": "wool",
    "sand": "soil",
    "dirt": "soil",
    "unknown": "unknown",
}


def normalize(name: str) -> str:
    """A reference name reduced to what the table matches on.

    Handles a path, an extension and a namespaced id: `minecraft:block/deepslate`,
    `../refs/Deepslate.png` and `deepslate` all reduce to `deepslate`.
    """
    text = str(name).lower().replace("\\", "/").rsplit("/", 1)[-1]
    text = re.sub(r"\.(png|jpg|jpeg|webp|tga)$", "", text)
    text = text.rsplit(":", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def classify(name: str) -> str:
    """What the reference IS, from its name. ``unknown`` when the table has no idea.

    ``unknown`` is honest: the engine says it does not know rather than guessing,
    and no plan is failed merely for attaching something the table has never heard
    of -- only for declaring a class the thing does not have.
    """
    normalized = normalize(name)
    for reference_class, patterns in _CLASS_RULES:
        for pattern in patterns:
            if re.search(pattern, normalized):
                return reference_class
    return "unknown"


def layer_of(name: str) -> str:
    """Which layer the reference belongs to, from its name.

    Checked before the class, because the layer is what the name announces first:
    `deepslate_iron_ore` is an ore (class) that lives deep (layer).
    """
    normalized = normalize(name)
    for layer, patterns in _LAYER_RULES:
        for pattern in patterns:
            if re.search(pattern, normalized):
                return layer
    return _CLASS_LAYER.get(classify(normalized), "unknown")


def same_layer(left: str, right: str) -> bool:
    """Do two layer names agree? Two `unknown`s do not count as agreement.

    Refusing to guess is the point: a check that says "these match" because it
    could not tell is worse than no check.
    """
    if "unknown" in {left, right}:
        return False
    return left == right


def describe_table() -> list[tuple[str, str]]:
    """The published table as (class, readable pattern list), for printing."""
    return [(name, " | ".join(patterns)) for name, patterns in _CLASS_RULES]


def describe_layers() -> list[tuple[str, str]]:
    """The published layer rules as (layer, readable pattern list), for printing."""
    return [(name, " | ".join(patterns)) for name, patterns in _LAYER_RULES]


def classes_of(names: Iterable[str]) -> dict[str, str]:
    """{name: class} for a set of names -- what the pool report prints."""
    return {name: classify(name) for name in names}


def layers_of(names: Iterable[str]) -> dict[str, str]:
    """{name: layer} for a set of names."""
    return {name: layer_of(name) for name in names}
