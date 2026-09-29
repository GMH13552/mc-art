"""The project settings contract, and surviving a pack that is being edited.

Two things are under test here and they are the same thing from two sides:

* the strategy file decides what generation may look at, and
* the project's own output is read fresh every run, so a sprite that was
  changed or deleted a second ago is not served from a stale answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asset_tree import png_bytes, write_png

from mc_art.asset_groups import AssetRoot
from mc_art.project_settings import (
    SETTINGS_SCHEMA,
    generated_source,
    load_project_settings,
    plan_sources,
    reference_sources,
)


def write_settings(project: Path, **reference) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    path = project / "mc-art.settings.json"
    path.write_text(
        json.dumps({"schema": SETTINGS_SCHEMA, "reference": reference}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def make_pack(project: Path, namespace: str = "demo", names=("one",)) -> Path:
    for name in names:
        target = project / "pack" / "assets" / namespace / "textures" / "block" / ("%s.png" % name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png_bytes())
    return project / "pack"


# -- the contract ---------------------------------------------------------


def test_absent_file_means_documented_defaults(tmp_path: Path) -> None:
    settings = load_project_settings(tmp_path)
    assert settings.present is False
    assert settings.include_generated is True
    assert settings.include_mods is True
    assert settings.directory == ""
    assert settings.notes == []


def test_unreadable_file_degrades_instead_of_raising(tmp_path: Path) -> None:
    (tmp_path / "mc-art.settings.json").write_text("{ not json", encoding="utf-8")
    settings = load_project_settings(tmp_path)
    assert settings.include_generated is True
    assert settings.notes and "unreadable" in settings.notes[0]


def test_unknown_schema_is_refused_rather_than_half_read(tmp_path: Path) -> None:
    (tmp_path / "mc-art.settings.json").write_text(
        json.dumps({"schema": "mc-art.settings/99", "reference": {"includeGenerated": False}}),
        encoding="utf-8",
    )
    settings = load_project_settings(tmp_path)
    assert "unknown schema" in settings.notes[0]
    # Half-reading a future file is how a strategy file becomes a lie: the
    # one field it did understand must NOT be applied.
    assert settings.include_generated is True


def test_a_namespace_nobody_toggled_counts_as_on(tmp_path: Path) -> None:
    write_settings(tmp_path, includeMods=True, mods={"jei": False})
    settings = load_project_settings(tmp_path)
    assert settings.mod_enabled("jei") is False
    assert settings.mod_enabled("aoa3") is True


# -- generated content is its own group -----------------------------------


def test_include_generated_false_withholds_the_project_pack(tmp_path: Path) -> None:
    make_pack(tmp_path)
    write_settings(tmp_path, includeGenerated=False)
    settings = load_project_settings(tmp_path)
    assert generated_source(tmp_path, settings) is None


def test_include_generated_true_offers_the_project_pack(tmp_path: Path) -> None:
    pack = make_pack(tmp_path)
    write_settings(tmp_path, includeGenerated=True)
    settings = load_project_settings(tmp_path)
    assert generated_source(tmp_path, settings) == pack


def test_generated_stays_out_of_the_external_list(tmp_path: Path) -> None:
    """The two groups have opposite lifecycles; the type must not merge them."""
    make_pack(tmp_path)
    version = tmp_path / "game" / "versions" / "1.12.2-forge"
    (version / "mods").mkdir(parents=True)
    write_settings(tmp_path, directory=str(version.parent.parent), includeGenerated=True)

    plan = plan_sources(tmp_path)
    assert plan.generated and plan.generated[0].name == "pack"
    assert all(item.name != "pack" for item in plan.external)


def test_several_versions_picks_the_one_being_modded(tmp_path: Path) -> None:
    """Not a guess: the version whose mods/ was touched last is the one in use.

    Stacking them all would let 1.18.2's jar override 1.12.2's, since later
    wins.  The choice is announced and the alternatives are named.
    """
    import os
    import zipfile

    game = tmp_path / "game"
    for name in ("1.12.2-forge", "1.18.2-forge"):
        (game / "versions" / name / "mods").mkdir(parents=True)
        (game / "versions" / name / "client.jar").write_bytes(b"")
    live = game / "versions" / "1.12.2-forge" / "mods"
    with zipfile.ZipFile(live / "jei.jar", "w") as bundle:
        bundle.writestr("assets/jei/textures/gui/x.png", png_bytes())
    old = game / "versions" / "1.18.2-forge" / "mods"
    os.utime(old, (1_000_000, 1_000_000))

    write_settings(tmp_path, directory=str(game))
    settings = load_project_settings(tmp_path)
    sources, rows = reference_sources(settings)
    names = sorted(item.name for item in sources)
    assert "jei.jar" in names
    assert all("1.18.2-forge" not in str(item) for item in sources)
    assert any("chosen because" in note for note in settings.notes)
    assert any("not used: 1.18.2-forge" in row for row in rows)


def test_reference_version_pins_the_choice(tmp_path: Path) -> None:
    game = tmp_path / "game"
    for name in ("1.12.2-forge", "1.18.2-forge"):
        (game / "versions" / name).mkdir(parents=True)
    jar = game / "versions" / "1.18.2-forge" / "client.jar"
    jar.write_bytes(b"")
    write_settings(tmp_path, directory=str(game), version="1.18.2-forge")

    settings = load_project_settings(tmp_path)
    assert settings.version == "1.18.2-forge"
    sources, rows = reference_sources(settings)
    assert sources == [jar]
    assert any("pinned by reference.version" in row for row in rows)


def test_naming_a_version_resolves_it(tmp_path: Path) -> None:
    game = tmp_path / "game"
    for name in ("1.12.2-forge", "1.18.2-forge"):
        (game / "versions" / name).mkdir(parents=True)
    jar = game / "versions" / "1.18.2-forge" / "client.jar"
    jar.write_bytes(b"")
    write_settings(tmp_path, directory=str(game))

    settings = load_project_settings(tmp_path)
    sources, rows = reference_sources(settings, version="1.18.2-forge")
    assert sources == [jar]
    assert any("pinned by --version" in row for row in rows)

    sources, _rows = reference_sources(settings, version="nope")
    assert sources == []
    assert any("no version named" in note for note in settings.notes)


def test_a_mod_jar_with_no_assets_is_not_a_source(tmp_path: Path) -> None:
    import zipfile

    version = tmp_path / "1.12.2-forge"
    (version / "mods").mkdir(parents=True)
    (version / "client.jar").write_bytes(b"")
    with zipfile.ZipFile(version / "mods" / "code-only.jar", "w") as bundle:
        bundle.writestr("com/example/Mod.class", b"\xca\xfe\xba\xbe")
    write_settings(tmp_path, directory=str(version))

    settings = load_project_settings(tmp_path)
    sources, _rows = reference_sources(settings)
    assert [item.name for item in sources] == ["client.jar"]


def test_a_corrupt_jar_is_kept_and_flagged_not_dropped(tmp_path: Path) -> None:
    """A half-downloaded jar in mods/ must not abort the run, and must not
    disappear without a word either."""
    version = tmp_path / "1.12.2-forge"
    (version / "mods").mkdir(parents=True)
    (version / "client.jar").write_bytes(b"")
    (version / "mods" / "broken.jar").write_bytes(b"PK\x03\x04 truncated")
    write_settings(tmp_path, directory=str(version))

    settings = load_project_settings(tmp_path)
    sources, rows = reference_sources(settings)
    assert [item.name for item in sources] == ["client.jar", "broken.jar"]
    assert any("UNREADABLE" in row for row in rows)


# -- a pack that is being edited -----------------------------------------


def test_reading_a_texture_that_was_just_deleted_returns_none(tmp_path: Path) -> None:
    """AssetRoot snapshots its keys but reads from disk.

    An unguarded read turned "someone deleted a sprite while the scan was
    running" into a FileNotFoundError that killed the whole run.
    """
    pack = make_pack(tmp_path, names=("keep", "gone"))
    root = AssetRoot(pack)
    try:
        assert root.read("assets/demo/textures/block/keep.png") is not None
        (pack / "assets" / "demo" / "textures" / "block" / "gone.png").unlink()
        assert root.read("assets/demo/textures/block/gone.png") is None
        # and the dead key is dropped, so it is not retried forever
        assert "assets/demo/textures/block/gone.png" not in root.keys()
    finally:
        root.close()


def test_index_walk_reports_a_texture_that_vanished(tmp_path: Path) -> None:
    """A path listed by rglob can be unreadable by the time it is opened.

    A directory literally named `x.png` reproduces that without a race.
    """
    from mc_art import reference_index as module

    pack = make_pack(tmp_path, names=("good",))
    trap = pack / "assets" / "demo" / "textures" / "block" / "trap.png"
    trap.mkdir()

    source = module.fingerprint_source(pack)
    rows, vanished = module._directory_entries(source)
    assert [row[1] for row in rows] == ["textures/block/good.png"]
    assert vanished == ["demo/textures/block/trap.png"]


def test_editing_a_texture_changes_the_bytes_offered(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pack = make_pack(project, names=("one",))
    sprite = pack / "assets" / "demo" / "textures" / "block" / "one.png"

    first = AssetRoot(pack)
    try:
        before = first.read("assets/demo/textures/block/one.png")
    finally:
        first.close()

    sprite.write_bytes(png_bytes(colour=(10, 20, 30, 255)))

    second = AssetRoot(pack)
    try:
        after = second.read("assets/demo/textures/block/one.png")
    finally:
        second.close()

    assert before is not None and after is not None
    assert before != after
