"""Platform gates: the documented entry point must actually run, and the docs
must not name an interpreter that does not exist.

These are the two checks that would have caught the real defect: a skill whose
docs said `M=bin/mc-art`, on a machine where that file cannot execute, wrapping a
`python3` that is a 0-byte Microsoft Store stub exiting 9009. Everything was
"present" and nothing was usable.

    python scripts/check-platform.py            # the gates
    python scripts/check-platform.py --fault    # reverse fixtures, both must go red
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The entry point the docs must be telling people to use, per platform.
PRIMARY_ENTRY = {
    "win32": ROOT / "bin" / "mc-art.cmd",
    "posix": ROOT / "bin" / "mc-art",
}

# Interpreter names that are not a portable entry point on Windows, and the temp
# directory that does not exist there.
FORBIDDEN_IN_DOCS = (
    (re.compile(r"\bpython3\b"), "python3 (a 0-byte Store stub on Windows: exit 9009)"),
    (re.compile(r"/tmp/"), "/tmp/ (does not exist on Windows)"),
)

DOCS = ("SKILL.md", "README.md")


def primary_entry() -> Path:
    return PRIMARY_ENTRY["win32" if sys.platform == "win32" else "posix"]


def probe(entry: Path) -> tuple[bool, str]:
    """Run the entry point for real. Exit 0 with a usage line, or it is not an entry."""
    if not entry.exists():
        return False, "missing: %s" % entry
    if entry.suffix == ".cmd":
        command = ["cmd", "/c", str(entry), "--help"]
    elif entry.suffix == ".ps1":
        command = ["powershell", "-NoProfile", "-File", str(entry), "--help"]
    else:
        command = [str(entry), "--help"]
    try:
        done = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            # The console codec on a Chinese Windows is GBK, and the engine's
            # usage text is not ASCII. Decode explicitly, or the gate itself
            # crashes on the platform it is meant to guard.
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except OSError as error:
        return False, "could not run: %s" % error
    except subprocess.TimeoutExpired:
        return False, "timed out"
    stdout = done.stdout or ""
    stderr = done.stderr or ""
    if done.returncode != 0:
        return False, "exit %d: %s" % (
            done.returncode,
            (stderr or stdout).strip()[:200],
        )
    if "mc_art" not in (stdout + stderr):
        return False, "exit 0 but no usage output: %r" % stdout[:120]
    return True, "exit 0, usage printed"


def check_entrypoint(entry: Path | None = None) -> tuple[bool, str]:
    entry = entry or primary_entry()
    ok, detail = probe(entry)
    return ok, "%s --help -> %s" % (entry.relative_to(ROOT), detail)


def _fenced_lines(text: str):
    """Yield (line number, line) for lines inside ``` fences.

    The ban is on *invocations*: a fenced block is what a reader copies, so that
    is where these tokens must never appear. Prose explaining why `python3` is a
    trap is useful and stays legal -- the rule is about what you would paste.
    """
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            yield number, line


def check_docs(docs: tuple[str, ...] = DOCS) -> tuple[bool, str]:
    problems: list[str] = []
    for name in docs:
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for number, line in _fenced_lines(text):
            for pattern, why in FORBIDDEN_IN_DOCS:
                if pattern.search(line):
                    problems.append("%s:%d runs %s" % (name, number, why))
    if problems:
        return False, "; ".join(problems)
    return True, "no command block in %s names a broken interpreter or a missing temp path" % ", ".join(DOCS)


def _fault_entrypoint() -> tuple[bool, str]:
    """The old entry point, verbatim: a shebang Windows cannot execute wrapping
    a python3 that is a Store stub. It must be rejected."""
    with tempfile.TemporaryDirectory() as scratch:
        broken = Path(scratch) / "mc-art-fault.cmd"
        broken.write_text(
            "@echo off\r\nrem the entry point as it shipped\r\npython3 -m mc_art %*\r\n",
            encoding="ascii",
        )
        ok, detail = probe(broken)
    return (not ok), detail


def _fault_docs() -> tuple[bool, str]:
    """A doc that names `python3` as the entry point must be rejected."""
    with tempfile.TemporaryDirectory() as scratch:
        fake = Path(scratch) / "SKILL.md"
        fake.write_text(
            "Run the engine:\n\n    python3 scripts/magnify.py ...\n", encoding="utf-8"
        )
        problems = [
            line
            for line in fake.read_text(encoding="utf-8").splitlines()
            if any(pattern.search(line) for pattern, _ in FORBIDDEN_IN_DOCS)
        ]
    fired = bool(problems)
    return fired, "found %d forbidden line(s): %s" % (len(problems), problems)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true", help="run the reverse fixtures")
    args = parser.parse_args()

    if args.fault:
        print("REVERSE FIXTURE 1: an entry point that shells out to python3")
        ok, detail = _fault_entrypoint()
        print("  %s  %s" % ("PASS (went red as required)" if ok else "FAIL (did not go red)", detail))
        first = ok
        print()
        print("REVERSE FIXTURE 2: a doc naming python3 as the entry point")
        ok2, detail2 = _fault_docs()
        print("  %s  %s" % ("PASS (went red as required)" if ok2 else "FAIL (did not go red)", detail2))
        print()
        print("fault gates: %s" % ("both red as required" if (first and ok2) else "ONE DID NOT FIRE"))
        return 0 if (first and ok2) else 1

    failures = 0
    for label, (ok, detail) in (
        ("platform entry point", check_entrypoint()),
        ("documentation", check_docs()),
    ):
        print("%-24s %s  %s" % (label, "PASS" if ok else "FAIL", detail))
        failures += 0 if ok else 1
    print()
    print("entry points shipped: %s" % ", ".join(
        str(path.relative_to(ROOT)) for path in sorted((ROOT / "bin").glob("mc-art*"))
    ))
    print("primary on this platform: %s" % primary_entry().relative_to(ROOT))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
