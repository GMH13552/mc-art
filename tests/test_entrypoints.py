"""The platform gates, as tests.

The defect this pins down was not in the art: the engine was fine, shipped fine,
and could not be invoked on a machine that had Python installed. The skill said
`M=bin/mc-art`; on Windows that is a bash script, and the `python3` inside it is a
0-byte Microsoft Store stub that exits 9009. "Present" and "usable" are different
properties, so both are asserted here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_platform", ROOT / "scripts" / "check-platform.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_platform = _load()


def test_the_documented_entry_point_for_this_platform_actually_runs() -> None:
    ok, detail = check_platform.check_entrypoint()
    assert ok, detail


def test_every_shipped_entry_point_exists() -> None:
    names = {path.name for path in (ROOT / "bin").glob("mc-art*")}
    assert {"mc-art", "mc-art.cmd", "mc-art.ps1"} <= names, names


def test_the_docs_do_not_ship_a_command_block_that_cannot_run() -> None:
    ok, detail = check_platform.check_docs()
    assert ok, detail


def test_an_entry_point_that_shells_out_to_python3_is_rejected() -> None:
    """The reverse fixture: the entry point as it shipped must fail the gate.

    On this machine that is literally exit 9009, because `python3` is the Store
    stub -- which is the whole reason the probe runs the interpreter instead of
    looking for it.
    """
    fired, detail = check_platform._fault_entrypoint()
    assert fired, detail


def test_a_doc_naming_python3_is_rejected() -> None:
    fired, detail = check_platform._fault_docs()
    assert fired, detail


@pytest.mark.skipif(sys.platform != "win32", reason="Windows entry point")
def test_the_windows_entry_points_are_probed_not_assumed(tmp_path: Path) -> None:
    """A .cmd that finds python3 and trusts it must fail, even though python3 exists."""
    broken = tmp_path / "assume.cmd"
    broken.write_text(
        "@echo off\r\n"
        "where python3 >nul 2>&1 && python3 -m mc_art %*\r\n"
        "rem 'the command exists' is not evidence: this exits 9009\r\n",
        encoding="ascii",
    )
    ok, detail = check_platform.probe(broken)
    assert ok is False, "a PATH-presence check must not pass as a working entry point: %s" % detail
