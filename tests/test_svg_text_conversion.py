"""Canonical SVG text conversion: chaining, retention and failure boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from infinity_symbol_tools import svg_font_audit, svg_processor
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_duplicates import detect_effective_symbol_duplicates
from infinity_symbol_tools.svg_font_audit import audit_effective_symbol_fonts
from infinity_symbol_tools.svg_text_conversion import convert_canonical_symbols
from test_svg_font_audit import GENERIC, NO_TEXT, _fixture

PATH_ONLY = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    files = _fixture(tmp_path, {
        "units/a.svg": GENERIC,
        "units/b.svg": GENERIC,
        "units/c.svg": NO_TEXT,
    })
    archive, manifest, report, preflight, preflight_state, aliases = files
    monkeypatch.setattr(
        svg_font_audit.svg_processor, "load_font_index",
        lambda: ({}, {}, 0, 0),
    )
    font = audit_effective_symbol_fonts(
        archive, manifest, report, preflight, preflight_state,
        tmp_path / "font.json", tmp_path / "font-state.json",
        alias_config=aliases,
    )
    assert font.status == "passed"
    monkeypatch.setattr(svg_processor, "pillow_image_module", lambda: object())
    monkeypatch.setattr(
        svg_processor, "resolve_duplicate_renderer", lambda _: ("resvg", "resvg-test"),
    )
    monkeypatch.setattr(svg_processor, "executable_version", lambda _: "test-1.0")

    def render(_renderer, _executable, _source, target, _size):
        target.write_bytes(b"png")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(svg_processor, "render_svg_for_duplicate_check", render)
    monkeypatch.setattr(svg_processor, "rgba_pixel_hash", lambda path: path.stem)
    duplicates = detect_effective_symbol_duplicates(
        archive, manifest, report, font.report, font.receipt,
        tmp_path / "duplicate-reports", tmp_path / "duplicate-state.json",
        render_size=128, jobs=1,
    )
    assert duplicates.status == "passed"
    assert duplicates.canonical_by_archive_path == {
        "units/a.svg": "units/a.svg",
        "units/b.svg": "units/a.svg",
        "units/c.svg": "units/c.svg",
    }
    return (
        archive, manifest, report, font.report, font.receipt,
        duplicates.receipt, duplicates.groups, duplicates.errors,
        duplicates.summary_csv, aliases,
    )


def _fake_converter(monkeypatch: pytest.MonkeyPatch, *, preserve_text=False):
    monkeypatch.setattr(
        svg_processor, "normalize_svg_for_conversion",
        lambda source, target, _exact, _compact: (
            shutil.copy2(source, target)
            and {"aliases_normalized": "", "warnings": ""}
        ),
    )
    monkeypatch.setattr(
        svg_processor, "resolve_text_converter",
        lambda _: ("usvg", "usvg-test"),
    )
    monkeypatch.setattr(
        svg_processor, "executable_version", lambda _: "test-1.0",
    )

    def export(_executable, source, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes() if preserve_text else PATH_ONLY)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(svg_processor, "usvg_text_to_path", export)


def _convert(tmp_path: Path, files):
    return convert_canonical_symbols(
        *files[:9],
        tmp_path / "CANONICAL.zip",
        tmp_path / "conversion.csv",
        tmp_path / "conversion-summary.csv",
        tmp_path / "conversion-state.json",
        aliases=files[9], text_converter="usvg", jobs=1,
    )


def test_converts_only_canonical_text_and_retains_untouched_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch)
    original = files[0].read_bytes()
    result = _convert(tmp_path, files)
    assert result.status == "passed"
    assert result.summary["textCandidateCount"] == 1
    assert result.summary["convertedCount"] == 1
    assert files[0].read_bytes() == original
    assert result.output_archive is not None
    with zipfile.ZipFile(result.output_archive) as archive:
        assert sorted(archive.namelist()) == ["units/a.svg", "units/c.svg"]
        assert archive.read("units/a.svg") == PATH_ONLY
        assert archive.read("units/c.svg") == NO_TEXT
    receipt = json.loads(result.receipt.read_text())
    assert receipt["stage"] == "textConversion"
    assert receipt["status"] == "passed"
    assert receipt["outputArtifact"] == {
        "name": result.output_archive.name,
        "sha256": sha256_file(result.output_archive),
    }
    assert receipt["converter"] == "usvg"
    assert receipt["converterVersion"] == "test-1.0"
    assert receipt["canonicalByArchivePath"]["units/b.svg"] == "units/a.svg"
    assert receipt["duplicateReceipt"]["sha256"] == sha256_file(files[5])
    assert receipt["fontAuditReceipt"]["sha256"] == sha256_file(files[4])


def test_duplicate_receipt_cannot_repoint_canonical_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch)
    forged = json.loads(files[5].read_text())
    forged["canonicalByArchivePath"]["units/b.svg"] = "units/c.svg"
    files[5].write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate CSV disagrees"):
        _convert(tmp_path, files)
    assert not (tmp_path / "CANONICAL.zip").exists()


def test_changed_alias_policy_fails_before_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch)
    files[9].write_text('{"schemaVersion": 1, "overrides": [{"source": "changed"}]}')
    with pytest.raises(ValueError, match="Font aliases have changed"):
        _convert(tmp_path, files)
    assert not (tmp_path / "conversion-state.json").exists()


def test_remaining_active_text_fails_closed_and_retains_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch, preserve_text=True)
    result = _convert(tmp_path, files)
    assert result.status == "failed"
    assert result.output_archive is None
    assert not (tmp_path / "CANONICAL.zip").exists()
    assert result.report.exists()
    receipt = json.loads(result.receipt.read_text())
    assert receipt["outputArtifact"] is None
    assert receipt["summary"]["conversionFailureCount"] == 1


def test_output_paths_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _setup(tmp_path, monkeypatch)
    _fake_converter(monkeypatch)
    first = _convert(tmp_path, files)
    assert first.status == "passed"
    with pytest.raises(FileExistsError, match="does not overwrite"):
        _convert(tmp_path, files)
