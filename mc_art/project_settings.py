"""The generation-strategy contract a project writes for itself.

`<project>/mc-art.settings.json` is the third of the three truths.  The atlas
says *what exists*, `lang` says *what it is called*, and this file says **what
generation is allowed to look at**.  The viewer writes it; the engine reads it.

Nothing else interprets it, so a missing, half-written or future-schema file
must degrade to the documented defaults and say so, never abort a run.

Two different things are being pointed at, and conflating them was a real bug:

* ``reference.directory`` -- the **external** baseline (vanilla + mods).
* ``includeGenerated``   -- the project's **own** already-produced textures,
  which join the reference set as ``pixel_style`` + ``palette`` so a new block
  matches the ones already drawn instead of drifting toward vanilla stone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zipfile import BadZipFile

from .asset_groups import AssetRoot

SETTINGS_SCHEMA = "mc-art.settings/1"
SETTINGS_FILENAME = "mc-art.settings.json"

# Files a modpack directory can contain that are not player-facing assets.
_JAR_SKIP = ("-sources", "-javadoc", "-dev", "-api", "-natives", "-slim")


def _is_asset_jar(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".jar"):
        return False
    stem = name[: -len(".jar")]
    return not any(stem.endswith(skip) for skip in _JAR_SKIP)


@dataclass
class ProjectSettings:
    """What the project will let generation see.  Defaults are the documented
    ones and are what an absent file means."""

    directory: str = ""
    version: str = ""
    include_generated: bool = True
    include_mods: bool = True
    mods: dict[str, bool] = field(default_factory=dict)
    path: Path | None = None
    present: bool = False
    notes: list[str] = field(default_factory=list)

    def mod_enabled(self, namespace: str) -> bool:
        """A namespace nobody ever toggled counts as on -- the setting is an
        opt-*out* list, so a newly installed mod is visible without a visit to
        the panel."""
        return self.mods.get(namespace, True)

    def describe(self) -> list[str]:
        rows = [
            "PROJECT SETTINGS -> %s (%s)" % (
                self.path if self.path else "(none)", "present" if self.present else "absent, using defaults"),
            "  includeGenerated=%s includeMods=%s directory=%s version=%s" % (
                "true" if self.include_generated else "false",
                "true" if self.include_mods else "false",
                self.directory or "(unset)",
                self.version or "(auto)",
            ),
        ]
        if self.mods:
            rows.append("  mods=%s" % ", ".join(
                "%s=%s" % (name, "on" if state else "off") for name, state in sorted(self.mods.items())))
        rows.extend("  note: %s" % note for note in self.notes)
        return rows


def load_project_settings(project_dir: str | Path) -> ProjectSettings:
    """Read one project's settings, tolerating every way it can be wrong."""
    root = Path(project_dir).expanduser().resolve()
    path = root / SETTINGS_FILENAME
    settings = ProjectSettings(path=path)
    if not path.is_file():
        return settings
    settings.present = True
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        settings.notes.append("%s is unreadable (%s); using defaults" % (path.name, exc))
        return settings
    if not isinstance(raw, dict):
        settings.notes.append("%s is not a JSON object; using defaults" % path.name)
        return settings

    schema = raw.get("schema")
    if schema != SETTINGS_SCHEMA:
        # Guessing at an unknown schema is how a strategy file silently becomes
        # a lie.  Say so and fall back rather than interpreting it halfway.
        settings.notes.append(
            "unknown schema %r (this engine reads %r); using defaults" % (schema, SETTINGS_SCHEMA))
        return settings

    reference = raw.get("reference")
    if not isinstance(reference, dict):
        settings.notes.append("no reference block in %s; using defaults" % path.name)
        return settings

    directory = reference.get("directory")
    if isinstance(directory, str):
        settings.directory = directory.strip()
    elif directory is not None:
        settings.notes.append("reference.directory is not a string; ignoring it")

    version = reference.get("version")
    if isinstance(version, str):
        settings.version = version.strip()
    elif version is not None:
        settings.notes.append("reference.version is not a string; ignoring it")

    settings.include_generated = reference.get("includeGenerated") is not False
    settings.include_mods = reference.get("includeMods") is not False

    mods = reference.get("mods")
    if isinstance(mods, dict):
        for name, state in mods.items():
            if isinstance(state, bool):
                settings.mods[str(name)] = state
            else:
                settings.notes.append("reference.mods[%r] is not a boolean; ignoring it" % name)
    elif mods is not None:
        settings.notes.append("reference.mods is not an object; ignoring it")
    return settings


def generated_source(project_dir: str | Path, settings: ProjectSettings) -> Path | None:
    """The project's own already-produced pack, or None when it is switched off.

    ``includeGenerated`` is the switch this function exists for: off means the
    project's own textures stay out of the reference set entirely.
    """
    if not settings.include_generated:
        return None
    root = Path(project_dir).expanduser().resolve()
    pack = root / "pack"
    if (pack / "assets").is_dir():
        return pack
    if (root / "assets").is_dir():
        return root
    return None


def _namespaces_of(path: Path) -> set[str] | None:
    """Namespaces a jar (or extracted tree) actually carries.

    Reuses the engine's own AssetRoot rather than re-implementing jar reading,
    so a modpack directory and a mod jar are described the same way.

    ``None`` means "could not be read", which is not the same as "carries no
    assets": a truncated download or a stray `.tmp` renamed to `.jar` must not
    be silently dropped, because the caller cannot then explain what happened
    to it.
    """
    try:
        root = AssetRoot(path)
    except (OSError, ValueError, BadZipFile):
        return None
    try:
        found: set[str] = set()
        for key in root.keys():
            if not key.startswith("assets/"):
                continue
            parts = PurePosixPath(key).parts
            if len(parts) >= 3:
                found.add(parts[1])
        return found
    finally:
        root.close()


def _mod_jars(mods_dir: Path) -> list[Path]:
    if not mods_dir.is_dir():
        return []
    return sorted(path for path in mods_dir.iterdir() if path.is_file() and _is_asset_jar(path))


def _keep_mod(jar: Path, settings: ProjectSettings) -> tuple[bool, list[str]]:
    """A mod jar is kept when any namespace inside it is switched on.

    Unreadable jars are kept and flagged rather than dropped: a run that
    quietly ignores half of `mods/` is worse than one that names the file it
    could not open.
    """
    namespaces = _namespaces_of(jar)
    if namespaces is None:
        return True, ["unreadable"]
    if not namespaces:
        return False, []
    enabled = sorted(name for name in namespaces if settings.mod_enabled(name))
    return bool(enabled), enabled


def reference_sources(
    settings: ProjectSettings,
    *,
    version: str | None = None,
    verbose: bool = False,
) -> tuple[list[Path], list[str]]:
    """Expand ``reference.directory`` into the concrete asset roots to stack.

    The directory may be any of the shapes a Minecraft installation actually
    uses, because there is no single one: for 1.12.2 the vanilla textures live
    inside the version jar, and Forge keeps each mod in ``versions/<v>/mods``.

    Order is a resource-pack stack -- later wins -- so vanilla comes first and
    mods after it.

    **A game directory holding several versions is refused, not guessed.**  A
    launcher that keeps 1.12.2 and 1.18.2 side by side (PCL does) would
    otherwise stack both, and since later wins, 1.18.2's JEI jar would quietly
    override 1.12.2's.  Nothing here is entitled to decide which version a
    project is for, so it says what it found and how to choose.
    """
    rows: list[str] = []
    directory = settings.directory
    if not directory:
        return [], rows
    root = Path(directory).expanduser()
    if not root.exists():
        settings.notes.append("reference.directory does not exist: %s" % root)
        return [], rows
    root = root.resolve()

    sources: list[Path] = []
    skipped: list[str] = []

    def add_mods(mods_dir: Path) -> None:
        for jar in _mod_jars(mods_dir):
            if not settings.include_mods:
                skipped.append(jar.name)
                continue
            keep, enabled = _keep_mod(jar, settings)
            if keep:
                sources.append(jar)
                if enabled == ["unreadable"]:
                    rows.append("    mod jar %s -> UNREADABLE, kept as-is" % jar.name)
                elif verbose and enabled:
                    rows.append("    mod jar %s -> %s" % (jar.name, ", ".join(enabled)))
            else:
                skipped.append(jar.name)

    def add_version(version_dir: Path) -> None:
        for jar in sorted(item for item in version_dir.iterdir()
                          if item.is_file() and _is_asset_jar(item)):
            sources.append(jar)
        add_mods(version_dir / "mods")

    if root.is_file():
        if root.suffix.lower() != ".jar":
            settings.notes.append("reference.directory is a file but not a jar: %s" % root)
            return [], rows
        sources.append(root)
        return sources, rows

    if (root / "assets").is_dir() and not (root / "versions").is_dir():
        sources.append(root)
        return sources, rows

    versions_dir = root / "versions"
    if versions_dir.is_dir() and any(item.is_dir() for item in versions_dir.iterdir()):
        found = sorted(item for item in versions_dir.iterdir() if item.is_dir())
        wanted = version or settings.version or None
        if wanted:
            match = next((item for item in found if item.name == wanted), None)
            if match is None:
                settings.notes.append(
                    "no version named %r under %s; found %s" % (
                        wanted, versions_dir, ", ".join(item.name for item in found)))
                return [], rows
            origin = "--version" if version else "reference.version"
            rows.append("  version -> %s (pinned by %s)" % (match.name, origin))
            add_version(match)
        elif len(found) == 1:
            rows.append("  version -> %s (only one installed)" % found[0].name)
            add_version(found[0])
        else:
            # Stacking every installed version would let 1.18.2's JEI jar
            # override 1.12.2's, since later wins.  Picking one from disk
            # evidence beats both guessing and refusing: a version you keep
            # mods in is a version you mod, and the one whose mods/ was touched
            # most recently is the one being worked on.  It is announced, the
            # alternatives are listed, and it can be pinned.
            with_mods = [item for item in found if _mod_jars(item / "mods")]
            pool = with_mods or found
            def _freshness(item: Path) -> tuple[float, str]:
                mods_dir = item / "mods"
                probe = mods_dir if mods_dir.is_dir() else item
                try:
                    return (probe.stat().st_mtime, item.name)
                except OSError:
                    return (0.0, item.name)
            chosen = max(pool, key=_freshness)
            basis = "its mods/ directory was touched most recently" if with_mods \
                else "it was touched most recently (no version here has mods)"
            others = [item.name for item in found if item.name != chosen.name]
            rows.append("  version -> %s (%s)" % (chosen.name, basis))
            if others:
                rows.append("    other installed versions, not used: %s" % ", ".join(others))
            settings.notes.append(
                "reference.directory is a game directory holding %d versions; %s was "
                "chosen because %s. Pin it with reference.version in the settings file "
                "or --version NAME." % (len(found), chosen.name, basis))
            add_version(chosen)
    elif any(item.is_file() and _is_asset_jar(item) for item in root.iterdir()):
        # A version directory, or a bare mods directory: both hold jars directly.
        add_version(root)
    else:
        add_mods(root / "mods")

    if skipped:
        rows.append("  skipped %d jar(s) by settings: %s" % (
            len(skipped), ", ".join(sorted(skipped)[:6]) + ("…" if len(skipped) > 6 else "")))
    return sources, rows


@dataclass
class SourcePlan:
    """The two reference groups, deliberately **not** merged into one list.

    They have opposite lifecycles.  The external baseline is a game
    installation: hundreds of megabytes, changed when you install a mod, and
    expensive to fingerprint.  The generated group is the project's own pack:
    tiny, and rewritten every few minutes while someone iterates on a sprite.
    Flattening them into one stack means every brush stroke invalidates the
    cache for 1406 vanilla textures -- so the type keeps them apart, and only
    a caller that genuinely wants one stack calls :meth:`stack`.
    """

    external: list[Path] = field(default_factory=list)
    generated: list[Path] = field(default_factory=list)
    explicit: list[Path] = field(default_factory=list)
    settings: ProjectSettings | None = None
    notes: list[str] = field(default_factory=list)

    def stack(self) -> list[Path]:
        """One ordered list for a resource-pack stack: later wins.

        Generated goes last so the project's own texture wins any name it
        shares with the game -- that is the point of having drawn it.
        """
        ordered: list[Path] = []
        for path in [*self.explicit, *self.external, *self.generated]:
            if path not in ordered:
                ordered.append(path)
        return ordered

    def is_generated(self, path: str | Path) -> bool:
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, ValueError):
            return False
        return any(resolved == item.resolve() for item in self.generated)

    def all_sources(self) -> list[Path]:
        return [*self.external, *self.generated, *self.explicit]

    def describe(self) -> list[str]:
        rows: list[str] = []
        if self.settings is not None:
            rows.extend(self.settings.describe())
        configured = (self.settings.directory if self.settings is not None else "")
        if self.external:
            rows.append("  external baseline -> %d source(s)" % len(self.external))
        elif configured:
            rows.append("  external baseline -> none usable from %s" % configured)
        else:
            rows.append("  external baseline -> none (reference.directory is unset)")
        for path in self.external[:4]:
            rows.append("    %s" % path)
        if len(self.external) > 4:
            rows.append("    … %d more" % (len(self.external) - 4))
        if self.generated:
            rows.append("  generated (volatile, re-read every run) -> %s" % self.generated[0])
        else:
            rows.append("  generated -> off")
        rows.extend("  %s" % note if note.startswith("  ") else "  note: %s" % note
                    for note in self.notes)
        return rows


def plan_sources(
    project_dir: str | Path | None,
    explicit: Iterable[str | Path] | None = None,
    *,
    include_generated: bool | None = None,
    include_mods: bool | None = None,
    version: str | None = None,
    verbose: bool = False,
) -> SourcePlan:
    """The one place a caller turns ``--project`` plus ``--source`` into a plan.

    Explicit ``--source`` values always come first: a caller who named a path
    outranks a strategy file.
    """
    plan = SourcePlan()
    for item in explicit or []:
        path = Path(item).expanduser().resolve()
        if path not in plan.explicit:
            plan.explicit.append(path)

    if project_dir is None or str(project_dir).strip() == "":
        return plan

    settings = load_project_settings(project_dir)
    if include_generated is not None:
        settings.include_generated = include_generated
    if include_mods is not None:
        settings.include_mods = include_mods
    plan.settings = settings

    external, notes = reference_sources(settings, version=version, verbose=verbose)
    plan.external = external
    plan.notes.extend(notes)

    generated = generated_source(project_dir, settings)
    if generated is not None:
        plan.generated = [generated]
    return plan
