"""The reference CLASS and NOTE gates, pinned to the real project's shapes.

Four plans from a real project, four different failures of the same kind: the
plan knew what it wanted and then attached something else, or explained itself
with a sentence that had been copied from a different plan. The engine had a
candidate table and a notes field and compared neither to anything.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mc_art.contracts import ReferenceAsset, ReferenceRole  # noqa: E402
from mc_art.refclass import classify, layer_of, same_layer  # noqa: E402
from mc_art.validation import validate_reference_class, validate_reference_notes  # noqa: E402


def _load_fixtures():
    spec = importlib.util.spec_from_file_location(
        "check_reference_class", ROOT / "scripts" / "check-reference-class.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fixtures = _load_fixtures()


def _ref(name: str, notes: list[str] | None = None, declared: str = "") -> ReferenceAsset:
    return ReferenceAsset(
        path="/nowhere/%s.png" % name,
        name=name,
        roles=[ReferenceRole.SHAPE, ReferenceRole.MATERIAL],
        notes=notes or [],
        declared_class=declared,
    )


# -- the published table -----------------------------------------------------

def test_the_class_table_separates_a_kind_from_a_layer() -> None:
    """`deepslate_iron_ore` is an ORE that lives DEEP -- both facts are needed.

    Flattening them makes "this asset is an ore from the wrong layer"
    unexpressible, which is the exact sentence the check has to be able to say.
    """
    assert classify("deepslate_iron_ore") == "ore_deposit"
    assert layer_of("deepslate_iron_ore") == "deep"
    assert classify("iron_ore") == "ore_deposit"
    assert layer_of("iron_ore") == "shallow"
    assert classify("deepslate") == "deep_stone"
    assert layer_of("deepslate") == "deep"


def test_shallow_and_deep_stone_are_different_layers() -> None:
    assert not same_layer(layer_of("stone"), layer_of("deepslate"))
    assert same_layer(layer_of("stone"), layer_of("cobblestone"))


def test_an_unknown_name_is_admitted_rather_than_guessed() -> None:
    """Saying "I do not know" is honest; guessing would make the check worthless."""
    assert classify("mysterious_artifact_thing") == "unknown"
    assert layer_of("mysterious_artifact_thing") == "unknown"
    assert not same_layer("unknown", "unknown")


# -- gate A: the class the plan declared -------------------------------------

def test_a_shallow_asset_that_attached_a_deep_reference_is_refused() -> None:
    """The real smoke_mist_stone, isolated.

    It declared itself a shallow-layer stone and attached deepslate.png, while its
    own note said the shallow variant "must NOT be chosen".
    """
    result = validate_reference_class(
        "shallow_stone",
        "shallow",
        [_ref("deepslate")],
        chosen_name="deepslate",
        available=[_ref("stone")],
        asset="smoke_mist_stone",
    )
    assert result.passed is False
    assert "declares class 'shallow_stone'" in result.errors[0]
    assert "deepslate=deep_stone" in result.errors[0]
    assert "stone (shallow_stone)" in result.errors[0], (
        "the report must name the reference that should have been used"
    )


def test_a_base_plus_deposit_plan_is_not_refused_for_attaching_the_base() -> None:
    """starfall_ore is an ore whose rock is deepslate.

    The declared class is the ore's, and the base is legitimately a different
    class -- requiring the chosen reference to carry it would refuse a correct
    plan.
    """
    result = validate_reference_class(
        "ore_deposit",
        "deep",
        [_ref("deepslate"), _ref("iron_ore")],
        chosen_name="deepslate",
        available=[],
        asset="starfall_ore",
    )
    assert result.passed is True, result.errors


def test_a_plan_may_not_relabel_the_reference_it_attached() -> None:
    result = validate_reference_class(
        "deep_stone",
        "deep",
        [_ref("deepslate", declared="shallow_stone")],
        chosen_name="deepslate",
        available=[],
    )
    assert result.passed is False
    assert "may not relabel" in result.errors[0]


def test_declaring_nothing_is_reported_as_unchecked() -> None:
    result = validate_reference_class(
        "", "", [_ref("deepslate")], chosen_name="deepslate", available=[]
    )
    assert result.passed is True
    assert result.metrics["checked"] is False
    assert "was NOT checked" in result.warnings[0]


# -- gate B: do the notes describe the reference they sit on -----------------

def test_a_note_that_forbids_the_reference_it_is_attached_to_is_refused() -> None:
    """mist_stone: a shallow stone carrying the note copied from abyss_stone."""
    note = (
        "same layer, same material role; a shallow-layer stone reference is the fault "
        "that already shipped once and must not be chosen"
    )
    result = validate_reference_notes([_ref("stone", [note])])
    assert result.passed is False
    assert "forbids it" in result.errors[0]
    assert "its own layer ('shallow')" in result.errors[0]


def test_the_same_note_on_the_correct_reference_is_accepted() -> None:
    """abyss_stone carries the identical sentence and is right to.

    The note forbids a SHALLOW stone; this reference is a deep one, so it forbids
    nothing that is here.
    """
    note = (
        "same layer, same material role; a shallow-layer stone reference is the fault "
        "that already shipped once and must not be chosen"
    )
    result = validate_reference_notes([_ref("deepslate", [note])])
    assert result.passed is True, result.errors


def test_a_false_claim_about_another_plans_reference_is_refused() -> None:
    """starfall_ore claims mist_stone's reference; mist_stone uses stone."""
    note = (
        "This is the same reference mist_stone uses, so the ore's rock is the family's rock"
    )
    result = validate_reference_notes(
        [_ref("deepslate", [note]), _ref("iron_ore")],
        plan_references={"mist_stone": ["stone"]},
    )
    assert result.passed is False
    assert "the claim is false" in result.errors[0]
    assert "that plan uses stone" in result.errors[0]


def test_the_same_claim_passes_when_it_is_true() -> None:
    note = "This is the same reference mist_stone uses"
    result = validate_reference_notes(
        [_ref("stone", [note])], plan_references={"mist_stone": ["stone"]}
    )
    assert result.passed is True, result.errors


def test_a_note_merely_mentioning_another_reference_is_not_a_contradiction() -> None:
    """The shipped ore says its specks sit "on the stone base".

    That names another attached reference and is a correct description of a
    composite choice. Firing on it would punish the notes that explain one.
    """
    note = (
        "the DEPOSITS come from this: its own speck pixels are overlaid on the stone base, "
        "so the values are vanilla's rather than hand-drawn"
    )
    result = validate_reference_notes([_ref("iron_ore", [note]), _ref("stone")])
    assert result.passed is True, result.errors


def test_a_note_arguing_for_a_different_reference_is_refused() -> None:
    """The dangerous copy: the note names another reference and says to use it."""
    note = "use deepslate instead; it is the deep-layer variant"
    result = validate_reference_notes([_ref("stone", [note]), _ref("deepslate")])
    assert result.passed is False
    assert "argues for 'deepslate'" in result.errors[0]


def test_an_uncheckable_cross_plan_claim_is_reported_not_assumed_true() -> None:
    note = "the same reference as mist_stone"
    result = validate_reference_notes([_ref("stone", [note])], plan_references={})
    assert result.passed is False
    assert "could not be checked" in result.errors[0]


# -- the four real plans together --------------------------------------------

def test_the_four_real_plans_refuse_exactly_the_three_that_are_wrong() -> None:
    """abyss_stone is clean; the other three are contradictions.

    This is the end-to-end shape: the gates must be sharp enough to pass the one
    plan that is right, or nobody will trust them on the three that are not.
    """
    import json
    import tempfile

    from mc_art.pipeline import GenerationPipeline
    from mc_art.planfile import plan_from_file

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        plans = fixtures.build_fixture_tree(root)
        pipeline = GenerationPipeline(critic=None, repairer=None, max_geometry_repairs=0)
        verdicts = {}
        for plan_path in sorted(plans.glob("*.plan.json")):
            run = pipeline.run(plan_from_file(plan_path), root / "out" / plan_path.stem, package=False)
            # `.stem` strips one suffix, so `x.plan.json` would key as `x.plan`.
            verdicts[plan_path.name[: -len(".plan.json")]] = run.validation.passed

    assert verdicts["abyss_stone"] is True, verdicts
    assert verdicts["mist_stone"] is False, verdicts
    assert verdicts["smoke_mist_stone"] is False, verdicts
    assert verdicts["starfall_ore"] is False, verdicts


def test_the_shipped_family_declares_its_class_and_passes() -> None:
    """The positive fixture: six plans that declare a class must stay green."""
    import json

    plans = ROOT / "examples" / "example_family" / "plans"
    if not plans.is_dir():
        pytest.skip("example family not present")
    checked = 0
    for path in sorted(plans.glob("*.plan.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        declared = data["descriptor"].get("class")
        if not declared:
            continue
        checked += 1
        references = [
            _ref(item["name"], declared=item.get("class", ""))
            for item in data.get("references", [])
        ]
        result = validate_reference_class(
            declared,
            data["descriptor"].get("layer", ""),
            references,
            chosen_name=references[0].name if references else "",
            available=[],
            asset=path.stem,
        )
        assert result.passed is True, (path.stem, result.errors)
    assert checked >= 6, "the shipped family should declare a class on every member"
