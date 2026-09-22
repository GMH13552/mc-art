"""Tests for tolerating model-shaped data at the wire boundary.

Both cases below killed a whole family member on a live dark oak armour run.
"""

from __future__ import annotations

from mc_art.appearance import _UNRESOLVED_COLOR, _palette_color
from mc_art.contracts import appearance_from_dict


def test_an_undefined_palette_token_degrades_instead_of_aborting() -> None:
    """A helmet died on ValueError: invalid color: 'oak_deep'."""
    assert _palette_color({"wood": "#8A6A3F"}, "wood") == (0x8A, 0x6A, 0x3F)
    assert _palette_color({}, "#123456") == (0x12, 0x34, 0x56)
    assert _palette_color({}, "oak_deep") == _UNRESOLVED_COLOR
    assert _palette_color({"wood": "#FFF"}, None) == _UNRESOLVED_COLOR
    assert _palette_color({"bad": "not-a-colour"}, "bad") == _UNRESOLVED_COLOR


def test_an_unrecognised_composite_mode_is_dropped_not_fatal() -> None:
    """Trousers died on 'part_reference_composite values must be overlay or replace'."""
    spec = appearance_from_dict({
        "palette": {"wood": "#8A6A3F"},
        "parts": {"plate": {"colors": ["wood"]}},
        "part_reference_composite": {"plate": "composite", "trim": "overlay"},
    })
    assert spec.part_reference_composite == {"trim": "overlay"}


def test_an_unrecognised_sampling_mode_is_dropped_not_fatal() -> None:
    spec = appearance_from_dict({
        "palette": {"wood": "#8A6A3F"},
        "parts": {"plate": {"colors": ["wood"]}},
        "part_reference_sampling": {"plate": "exact", "trim": "pattern"},
    })
    assert spec.part_reference_sampling == {"trim": "pattern"}
