"""`mc-art doctor`: answer, in one command, every environment question this project
has actually been bitten by.

Each check here exists because of a real failure that took a separate round to
find:

* **the entry points** -- `bin/mc-art` is a bash script and Windows cannot execute
  it; a `python3` that is a 0-byte Microsoft Store stub exits 9009, and "the
  command exists" is not evidence. (Rounds: the engine shipped and was unusable.)
* **line endings** -- a clone with `core.autocrlf=true` turned the extensionless
  launcher into CRLF, and the panel vendored that broken copy into the npm package.
* **which interpreter, and why the others were rejected** -- recorded, not assumed.
* **the engine root is writable** -- render and evidence write beside the plan; a
  read-only install fails halfway through a job.
* **two copies of the engine agree** -- a clone under `~/.dsh/skills/mc-art` and the
  vendored copy in the package both exist on a real machine, and a stale clone was
  one commit behind on the very night a package went out without the Windows entry
  point.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

# Candidate interpreters, in order. A candidate counts only if it RUNS.
CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("python",),
    ("python3",),
    ("py", "-3"),
    ("py",),
)

PROBE = "print(1)"


def _run(command: list[str], cwd: Path | None = None, timeout: int = 60):
    try:
        return subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return error


def probe_interpreter(candidate: tuple[str, ...]) -> tuple[bool, str]:
    """Does this interpreter actually run? Presence is not evidence."""
    if shutil.which(candidate[0]) is None:
        return False, "not on PATH"
    done = _run([*candidate, "-c", PROBE])
    if isinstance(done, Exception):
        return False, str(done)[:80]
    if done.returncode != 0:
        return False, "exit %d (a Store stub exits 9009)" % done.returncode
    if (done.stdout or "").strip() != "1":
        return False, "ran but printed %r, not 1" % (done.stdout or "")
    return True, "runs"


def resolve_interpreter() -> tuple[tuple[str, ...] | None, list[tuple[str, str]]]:
    rejected: list[tuple[str, str]] = []
    explicit = os.environ.get("MC_ART_PYTHON")
    if explicit:
        candidate = (explicit,)
        ok, why = probe_interpreter(candidate)
        if ok:
            return candidate, rejected
        rejected.append(("MC_ART_PYTHON=%s" % explicit, why))
    for candidate in CANDIDATES:
        ok, why = probe_interpreter(candidate)
        if ok:
            return candidate, rejected
        rejected.append((" ".join(candidate), why))
    return None, rejected


def check_entry_points(root: Path) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    primary = "bin/mc-art.cmd" if sys.platform == "win32" else "bin/mc-art"
    for name in ("bin/mc-art", "bin/mc-art.cmd", "bin/mc-art.ps1"):
        path = root / name
        if not path.exists():
            lines.append("  MISSING  %s" % name)
            ok = False
            continue
        is_primary = name == primary
        lines.append("  %-20s %s%s" % (name, "present", "   <- primary on this platform"
                                       if is_primary else ""))
    # The primary one is PROBED, not inspected.
    entry = root / primary
    if entry.exists():
        if entry.suffix == ".cmd":
            command = ["cmd", "/c", str(entry), "--help"]
        elif entry.suffix == ".ps1":
            command = ["powershell", "-NoProfile", "-File", str(entry), "--help"]
        else:
            command = [str(entry), "--help"]
        done = _run(command, cwd=root, timeout=120)
        if isinstance(done, Exception):
            lines.append("  %s --help -> could not run: %s" % (primary, str(done)[:80]))
            ok = False
        elif done.returncode != 0:
            lines.append("  %s --help -> exit %d: %s"
                         % (primary, done.returncode, (done.stderr or done.stdout or "")[:120]))
            ok = False
        else:
            lines.append("  %s --help -> exit 0" % primary)
    return ok, lines


def check_line_endings(root: Path) -> tuple[bool, list[str]]:
    rules = (("bin/mc-art", "lf"), ("bin/mc-art.cmd", "crlf"), ("bin/mc-art.ps1", "crlf"))
    lines: list[str] = []
    ok = True
    for rel, want in rules:
        path = root / rel
        if not path.exists():
            lines.append("  MISSING  %s" % rel)
            ok = False
            continue
        data = path.read_bytes()
        has_cr = b"\r" in data
        good = (want == "lf" and not has_cr) or (want == "crlf" and has_cr)
        declared = ""
        done = _run(["git", "check-attr", "eol", "--", rel], cwd=root)
        if not isinstance(done, Exception) and done.returncode == 0:
            declared = (done.stdout or "").strip().rsplit(":", 1)[-1].strip()
        if not good:
            ok = False
        if declared != want:
            ok = False
        lines.append("  %-20s eol=%-5s bytes=%s  git says %r%s"
                     % (rel, want, "CRLF" if has_cr else "LF", declared,
                        "" if (good and declared == want) else "   <- WRONG"))
    return ok, lines


def check_writable(root: Path) -> tuple[bool, list[str]]:
    try:
        with tempfile.NamedTemporaryFile(dir=str(root), prefix=".mc-art-doctor-", delete=True):
            pass
        return True, ["  %s is writable" % root]
    except OSError as error:
        return False, ["  %s is NOT writable: %s" % (root, error)]


def check_copies(root: Path) -> tuple[bool, list[str]]:
    """Do the copies of the engine on this machine agree?"""
    others = [
        Path.home() / ".dsh" / "skills" / "mc-art",
        root.parent / "skills" / "mc-art",
    ]
    lines: list[str] = []
    mine = _digest(root)
    lines.append("  this copy      %s  %s" % (mine[:16], root))
    if not (root / "mc_art").exists():
        return True, lines + ["  (no mc_art/ here to compare)"]
    found = False
    for other in others:
        if not other.exists() or other.resolve() == root.resolve():
            continue
        if not (other / "mc_art").exists():
            continue
        found = True
        theirs = _digest(other)
        same = theirs == mine
        lines.append("  %-14s %s  %s%s" % ("other copy", theirs[:16], other,
                                           "" if same else "   <- DIFFERENT"))
        if same:
            continue
        # Say WHICH side is stale rather than leaving it to be guessed.
        lines.extend(_staleness(root, other))
        return False, lines
    if not found:
        lines.append("  no second copy found (checked ~/.dsh/skills/mc-art and ../skills/mc-art)")
    return True, lines


def _staleness(root: Path, other: Path) -> list[str]:
    lines: list[str] = []
    dirty = _run(["git", "status", "--porcelain"], cwd=root)
    if not isinstance(dirty, Exception) and (dirty.stdout or "").strip():
        lines.append("    this working copy has uncommitted changes, so a difference here is "
                     "expected -- commit and re-vendor before comparing")
    here = _run(["git", "rev-parse", "HEAD"], cwd=root)
    there = _run(["git", "rev-parse", "HEAD"], cwd=other)
    here_head = (here.stdout or "").strip() if not isinstance(here, Exception) else ""
    there_head = (there.stdout or "").strip() if not isinstance(there, Exception) else ""
    if here_head and there_head and here_head != there_head:
        behind = _run(["git", "rev-list", "--count", "%s..%s" % (there_head, here_head)], cwd=root)
        count = (behind.stdout or "").strip() if not isinstance(behind, Exception) else "?"
        lines.append("    this copy is at %s, the other at %s -- the other is %s commit(s) behind"
                     % (here_head[:8], there_head[:8], count))
    return lines


def _digest(root: Path) -> str:
    """A digest of the engine's own source, so two copies can be compared."""
    hasher = hashlib.sha256()
    for path in sorted((root / "mc_art").glob("*.py")):
        hasher.update(path.name.encode("utf-8"))
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="mc-art doctor", description=__doc__)
    parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]

    print("mc-art doctor")
    print("  platform      %s" % sys.platform)
    print("  engine root   %s" % root)
    print("  modules       %d" % len(list((root / "mc_art").glob("*.py"))))
    print()

    failures = 0
    print("interpreter")
    interpreter, rejected = resolve_interpreter()
    if interpreter:
        print("  using %s" % " ".join(interpreter))
    else:
        print("  NONE WORKS -- mc-art cannot run")
        failures += 1
    for name, why in rejected:
        print("  rejected %-14s %s" % (name, why))
    print()

    for title, check in (
        ("entry points", lambda: check_entry_points(root)),
        ("line endings", lambda: check_line_endings(root)),
        ("writable", lambda: check_writable(root)),
        ("engine copies", lambda: check_copies(root)),
    ):
        ok, lines = check()
        print("%s  %s" % (title, "OK" if ok else "PROBLEM"))
        for line in lines:
            print(line)
        print()
        failures += 0 if ok else 1

    print("%s" % ("doctor: all checks passed" if not failures
                  else "doctor: %d problem(s) -- see above" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
