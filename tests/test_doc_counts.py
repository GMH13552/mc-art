"""The numbers the documentation states about the repository, checked against it.

`SKILL.md` and `README.md` both claimed the engine was "22 modules" while it was
33. That is not a typo someone made once: it is a literal that nothing compared to
the thing it describes, so it drifts every time a module is added. The fix is not
a fresh literal, it is a check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module_count() -> int:
    return len(list((ROOT / "mc_art").glob("*.py")))


def claimed_counts() -> list[tuple[str, int, str]]:
    """[(file, claimed number, the line)] for every "N modules" claim."""
    found = []
    pattern = re.compile(r"(\d+)\s+modules\b")
    for name in ("SKILL.md", "README.md"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = pattern.search(line)
            if match:
                found.append((name, int(match.group(1)), line.strip()))
    return found


def test_the_documentation_states_the_real_module_count() -> None:
    actual = module_count()
    claims = claimed_counts()
    assert claims, "the docs should say how big the engine is"
    wrong = [(name, number) for name, number, _line in claims if number != actual]
    assert not wrong, (
        "mc_art/ holds %d .py modules but the docs say %s. Update them -- or better, "
        "notice that this check is why the number can no longer drift."
        % (actual, wrong)
    )


def test_every_claimed_count_agrees_with_the_others() -> None:
    numbers = {number for _name, number, _line in claimed_counts()}
    assert len(numbers) <= 1, "the two documents disagree about the engine's size: %s" % numbers
