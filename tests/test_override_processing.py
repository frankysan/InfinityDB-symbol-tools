"""Versioned standalone override resolver compatibility fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from infinity_symbol_tools.override_processing import resolve_reviewed_overrides

BASE = "https://assets.corvusbelli.net/army/img/"
A = BASE + "logo/units/unit-a.svg"
B = BASE + "logo/units/unit-b.svg"
FACTION = BASE + "logo/factions/faction.svg"
STATIC = BASE + "icon/cube.svg"
UPSTREAM = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="2"/></svg>'
OVERRIDE = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


def _write(root: Path, relative: str, body: bytes = OVERRIDE) -> Path:
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(body)
    return file


def test_direct_and_shared_upstream_override_propagate_inside_category(
    tmp_path: Path,
) -> None:
    override_root = tmp_path / "overrides"
    _write(override_root, "units/unit-a.svg")
    upstream = {A: UPSTREAM, B: UPSTREAM, FACTION: UPSTREAM}
    lookup = {A: "units/unit-a.svg", B: "units/unit-b.svg", FACTION: "factions/faction.svg"}
    result = resolve_reviewed_overrides(upstream, lookup, override_root)
    assert result.direct_override_count == 1
    assert result.propagated_override_count == 1
    assert result.unused_overrides == ()
    for url in (A, B):
        symbol = result.symbols[url]
        assert symbol.body == OVERRIDE
        assert symbol.source_method == "override"
        assert symbol.upstream_sha256 == hashlib.sha256(UPSTREAM).hexdigest()
        assert symbol.effective_sha256 == hashlib.sha256(OVERRIDE).hexdigest()
        assert symbol.override_path == "units/unit-a.svg"
    assert result.symbols[FACTION].body == UPSTREAM
    assert result.symbols[FACTION].source_method == "network"


def test_static_override_lookup_path_is_distinct_from_raw_archive_path(
    tmp_path: Path,
) -> None:
    override_root = tmp_path / "overrides"
    _write(override_root, "characteristics/cube.svg")
    result = resolve_reviewed_overrides(
        {STATIC: UPSTREAM},
        {STATIC: "characteristics/cube.svg"},
        override_root,
    )
    assert result.symbols[STATIC].body == OVERRIDE
    assert result.symbols[STATIC].override_path == "characteristics/cube.svg"


def test_missing_upstream_can_still_use_a_direct_override(tmp_path: Path) -> None:
    root = tmp_path / "overrides"
    _write(root, "units/unit-a.svg")
    result = resolve_reviewed_overrides(
        {A: None, B: None},
        {A: "units/unit-a.svg", B: "units/unit-b.svg"},
        root,
    )
    assert result.symbols[A].body == OVERRIDE
    assert result.symbols[A].upstream_sha256 is None
    assert result.symbols[B].body is None
    assert result.symbols[B].effective_sha256 is None


def test_conflicting_upstream_equivalent_overrides_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "overrides"
    _write(root, "units/unit-a.svg", OVERRIDE)
    _write(root, "units/unit-b.svg", b"<svg id='different'/>")
    with pytest.raises(ValueError, match="Conflicting image overrides"):
        resolve_reviewed_overrides(
            {A: UPSTREAM, B: UPSTREAM},
            {A: "units/unit-a.svg", B: "units/unit-b.svg"},
            root,
        )


def test_unused_overrides_and_missing_root_are_accounted_for(tmp_path: Path) -> None:
    root = tmp_path / "overrides"
    without = resolve_reviewed_overrides({A: UPSTREAM}, {A: "units/unit-a.svg"}, root)
    assert without.symbols[A].body == UPSTREAM
    assert without.unused_overrides == ()

    _write(root, "units/unreferenced.svg")
    with_unused = resolve_reviewed_overrides(
        {A: UPSTREAM}, {A: "units/unit-a.svg"}, root,
    )
    assert with_unused.unused_overrides == ("units/unreferenced.svg",)


def test_unknown_lookup_url_and_unsafe_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="differ"):
        resolve_reviewed_overrides({A: UPSTREAM}, {B: "units/unit.svg"}, tmp_path)
    for name in ("../escape.svg", "units/../escape.svg", "/absolute.svg", "units\\oops.svg"):
        with pytest.raises(ValueError, match="Invalid override lookup path"):
            resolve_reviewed_overrides({A: UPSTREAM}, {A: name}, tmp_path)


def test_invalid_override_svg_fails(tmp_path: Path) -> None:
    root = tmp_path / "overrides"
    _write(root, "units/unit-a.svg", b"<html />")
    with pytest.raises(ValueError, match="not <svg>"):
        resolve_reviewed_overrides({A: UPSTREAM}, {A: "units/unit-a.svg"}, root)


def test_casefold_override_collision_fails_on_case_sensitive_filesystems(
    tmp_path: Path,
) -> None:
    root = tmp_path / "overrides"
    _write(root, "units/unit-a.svg")
    if (root / "units/UNIT-A.svg").exists():
        pytest.skip("filesystem cannot represent two case-colliding names")
    _write(root, "units/UNIT-A.svg")
    with pytest.raises(ValueError, match="Case-insensitive image override collision"):
        resolve_reviewed_overrides({A: UPSTREAM}, {A: "units/unit-a.svg"}, root)
