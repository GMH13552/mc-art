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


# Line endings, per file, and the attribute that must back each one. `bin/mc-art`
# has no extension, so it is named in full -- a `*.sh` pattern would not match it,
# which is exactly how a CRLF copy of it got vendored into the npm package.
LINE_ENDING_RULES = (
    ("bin/mc-art", "lf"),
    ("bin/mc-art.cmd", "crlf"),
    ("bin/mc-art.ps1", "crlf"),
    ("SKILL.md", "lf"),
    ("scripts/check-platform.py", "lf"),
)


def git_check_attr(rel: str, repo: Path | None = None) -> str:
    """What git says the `eol` attribute is for this path -- not what we assume.

    Asserting "I added a .gitattributes file" is not the same as asserting the
    rule takes effect. This asks git.
    """
    done = subprocess.run(
        ["git", "check-attr", "eol", "--", rel],
        cwd=str(repo or ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        return "error: %s" % (done.stderr or "").strip()[:80]
    # "bin/mc-art: eol: lf"
    return (done.stdout or "").strip().rsplit(":", 1)[-1].strip()


def check_line_endings() -> tuple[bool, str]:
    """The working tree and the attributes must agree, file by file.

    A CRLF shebang script does not run -- `bash: $'\\r': command not found` -- and
    the broken copy is what gets vendored into the npm package. So both halves are
    checked: the bytes on disk, and the rule that keeps them right on someone
    else's clone.
    """
    problems: list[str] = []
    for rel, want in LINE_ENDING_RULES:
        path = ROOT / rel
        if not path.exists():
            problems.append("%s is missing" % rel)
            continue
        data = path.read_bytes()
        has_cr = b"\r" in data
        if want == "lf" and has_cr:
            problems.append(
                "%s contains %d CR byte(s): a CRLF copy of this file does not run, and it is "
                "vendored as-is" % (rel, data.count(b"\r"))
            )
        if want == "crlf" and not has_cr:
            problems.append("%s is LF-only; cmd.exe and PowerShell expect CRLF" % rel)
        declared = git_check_attr(rel)
        if declared != want:
            problems.append(
                "%s: .gitattributes resolves eol to '%s', expected '%s' -- without the rule a "
                "clone with core.autocrlf=true gets the wrong endings" % (rel, declared, want)
            )
    if problems:
        return False, "; ".join(problems)
    return True, "%d file(s): endings on disk match the attributes git reports" % len(
        LINE_ENDING_RULES
    )


def _fault_line_endings() -> tuple[bool, str]:
    """Reverse fixture: a CRLF launcher, and a repository with no rule at all.

    Both are the real defect. The first is what an autocrlf clone produced; the
    second is the state of this repository before .gitattributes existed.
    """
    fired: list[str] = []
    with tempfile.TemporaryDirectory() as scratch:
        broken = Path(scratch) / "mc-art"
        broken.write_bytes((ROOT / "bin" / "mc-art").read_bytes().replace(b"\n", b"\r\n"))
        if b"\r" in broken.read_bytes():
            fired.append(
                "a CRLF launcher is detected (%d CR bytes)" % broken.read_bytes().count(b"\r")
            )

        # A repository that has the file but no rule: `git check-attr` answers
        # "unspecified", which is the bug in its original form.
        repo = Path(scratch) / "norule"
        (repo / "bin").mkdir(parents=True)
        (repo / "bin" / "mc-art").write_bytes((ROOT / "bin" / "mc-art").read_bytes())
        subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True)
        declared = git_check_attr("bin/mc-art", repo=repo)
        if declared != "lf":
            fired.append("a repository without the rule resolves eol to '%s'" % declared)
    return len(fired) == 2, "; ".join(fired)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fault", action="store_true", help="run the reverse fixtures")
    args = parser.parse_args()

    if args.fault:
        results = []
        print("REVERSE FIXTURE 1: an entry point that shells out to python3")
        ok, detail = _fault_entrypoint()
        print("  %s  %s" % ("PASS (went red as required)" if ok else "FAIL (did not go red)", detail))
        results.append(ok)
        print()
        print("REVERSE FIXTURE 2: a doc naming python3 as the entry point")
        ok2, detail2 = _fault_docs()
        print("  %s  %s" % ("PASS (went red as required)" if ok2 else "FAIL (did not go red)", detail2))
        results.append(ok2)
        print()
        print("REVERSE FIXTURE 3: a CRLF launcher, and a repo with no .gitattributes rule")
        ok3, detail3 = _fault_line_endings()
        print("  %s  %s" % ("PASS (went red as required)" if ok3 else "FAIL (did not go red)", detail3))
        results.append(ok3)
        print()
        print("fault gates: %s" % (
            "all red as required" if all(results) else "ONE DID NOT FIRE"
        ))
        return 0 if all(results) else 1

    failures = 0
    for label, (ok, detail) in (
        ("platform entry point", check_entrypoint()),
        ("documentation", check_docs()),
        ("line endings", check_line_endings()),
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
