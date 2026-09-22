"""Tests for the dynamic family label and for family (set) generation."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from asset_tree import write_png

from mc_art import family
from mc_art import metrics as quality
from mc_art.contracts import AppearanceSpec, PartAppearance, appearance_from_dict
from mc_art.reference_retrieval import (
    Candidate,
    build_router_manifest,
    family_token,
    manifest_label,
    parse_router_selection,
)


@dataclass(frozen=True)
class _FakeEntry:
    asset_id: str
    namespace: str = "minecraft"
    resource_path: str = ""
    category: str = "item"
    dimensions: dict = field(default_factory=dict)
    alpha: dict = field(default_factory=dict)
    palette: dict = field(default_factory=dict)
    structure: dict = field(default_factory=dict)


def _candidate(name: str, category: str = "item", score: float = 0.0) -> Candidate:
    return Candidate(
        _FakeEntry(
            asset_id="minecraft:%s/%s" % (category, name),
            resource_path="%s/%s.png" % (category, name),
            category=category,
        ),
        score,
        [],
        [],
    )


# -- dynamic classification -------------------------------------------

def test_family_consistency_separates_a_match_from_a_drifted_member(tmp_path: Path) -> None:
    anchor = tmp_path / "anchor.png"
    same = tmp_path / "same.png"
    drifted = tmp_path / "drifted.png"
    write_png(anchor, (200, 200, 200, 255))
    write_png(same, (200, 200, 200, 255))
    write_png(drifted, (10, 200, 30, 255))
    report = quality.family_consistency(anchor, [same, drifted], minimum_overlap=0.5)
    rows = {Path(row["sprite"]).name: row for row in report["members"]}
    assert rows["same.png"]["consistent"] is True
    assert rows["same.png"]["palette_overlap"] == 1.0
    assert rows["drifted.png"]["consistent"] is False
    assert rows["drifted.png"]["palette_overlap"] == 0.0


# -- family generation orchestration ----------------------------------

def _fake_member_report(out_dir: Path, colour) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    sprite = out_dir / "sprite.png"
    write_png(sprite, colour)
    return {
        "selected_round": 0,
        "rounds_completed": 1,
        "selected_sprite": str(sprite),
        "rounds": [{
            "index": 0,
            "validation": {"passed": True},
            "blind_review": {"primary_object": "helmet"},
        }],
    }


