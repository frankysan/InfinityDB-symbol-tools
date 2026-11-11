"""Standalone duplicate-stage receipt, parity and failure-boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infinity_symbol_tools import svg_duplicates, svg_font_audit, svg_processor
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_duplicates import detect_effective_symbol_duplicates
from infinity_symbol_tools.svg_font_audit import audit_effective_symbol_fonts
from test_svg_font_audit import _fixture

SAME = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
SAME_VISUAL = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0,0"/></svg>'
DIFFERENT = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ...]:
    files = _fixture(tmp_path, {
        "units/a.svg": SAME,
        "units/b.svg": SAME,
        "units/c.svg": SAME_VISUAL,
        "units/d.svg": DIFFERENT,
    })
    archive, manifest, report, preflight, receipt, aliases = files
    monkeypatch.setattr(svg_font_audit.svg_processor, "load_font_index", lambda: ({}, {}, 0, 0))
    audit = audit_effective_symbol_fonts(
        archive, manifest, report, preflight, receipt,
        tmp_path / "font-audit.json", tmp_path / "font-audit-state.json",
        alias_config=aliases,
    )
    assert audit.status == "passed"
    return archive, manifest, report, audit.report, audit.receipt


def _fake_renderer(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
    monkeypatch.setattr(svg_processor, "pillow_image_module", lambda: object())
    monkeypatch.setattr(
        svg_processor, "resolve_duplicate_renderer", lambda _renderer: ("resvg", "resvg"),
    )
    monkeypatch.setattr(svg_processor, "executable_version", lambda _path: "test-1.0")

    def render(_renderer, _executable, _source, destination, _size):
        if fail:
            return SimpleNamespace(returncode=1, stderr="simulated render failure", stdout="")
        destination.write_bytes(b"png")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(svg_processor, "render_svg_for_duplicate_check", render)
    monkeypatch.setattr(
        svg_processor, "rgba_pixel_hash",
        lambda path: "same" if int(path.stem.split("-")[-1]) in {1, 2} else "different",
    )


def _detect(tmp_path: Path, files: tuple[Path, ...]):
    return detect_effective_symbol_duplicates(
        files[0], files[1], files[2], files[3], files[4],
        tmp_path / "duplicate-reports", tmp_path / "duplicate-receipt.json",
        render_size=128, jobs=1, renderer="resvg",
    )


def test_real_duplicate_engine_retains_existing_visual_canonical_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    _fake_renderer(monkeypatch)
    initial = files[0].read_bytes()
    result = _detect(tmp_path, files)
    assert result.status == "passed"
    assert result.canonical_by_archive_path == {
        "units/a.svg": "units/a.svg",
        "units/b.svg": "units/a.svg",
        "units/c.svg": "units/a.svg",
        "units/d.svg": "units/d.svg",
    }
    assert result.summary == {
        "sourceAssetCount": 4,
        "uniqueByteSetCount": 3,
        "rendersAvoidedExactCount": 1,
        "exactGroupCount": 0,
        "visualGroupCount": 1,
        "redundantAssetCount": 2,
        "canonicalAssetCount": 2,
        "renderErrorCount": 0,
        "sourceAssetBytes": len(SAME) * 2 + len(SAME_VISUAL) + len(DIFFERENT),
        "canonicalAssetBytes": len(SAME) + len(DIFFERENT),
        "reclaimedAssetBytes": len(SAME) + len(SAME_VISUAL),
    }
    assert "representative,units/a.svg" in result.groups.read_text(encoding="utf-8-sig")
    assert "source_size_bytes,canonical_size_bytes" in result.summary_csv.read_text(
        encoding="utf-8-sig",
    )
    state = json.loads(result.receipt.read_text(encoding="utf-8"))
    assert state["format"] == "InfinityDB symbol processing stage receipt"
    assert state["stage"] == "duplicateDetection"
    assert state["status"] == "passed"
    assert state["renderer"] == "resvg"
    assert state["rendererVersion"] == "test-1.0"
    assert state["renderSize"] == 128
    assert state["jobs"] == 1
    assert state["canonicalByArchivePath"] == result.canonical_by_archive_path
    assert state["fontAuditReport"] == {
        "name": files[3].name, "sha256": sha256_file(files[3]),
    }
    assert state["fontAuditReceipt"] == {
        "name": files[4].name, "sha256": sha256_file(files[4]),
    }
    assert state["reports"]["groups"] == {
        "name": result.groups.name, "sha256": sha256_file(result.groups),
    }
    assert files[0].read_bytes() == initial


def test_unresolved_visual_rendering_marks_stage_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    _fake_renderer(monkeypatch, fail=True)
    result = _detect(tmp_path, files)
    assert result.status == "failed"
    assert result.summary["renderErrorCount"] == 4
    assert result.summary["exactGroupCount"] == 1
    assert result.summary["visualGroupCount"] == 0
    assert json.loads(result.receipt.read_text())["status"] == "failed"
    assert "simulated render failure" in result.errors.read_text(encoding="utf-8-sig")


def test_tampered_font_report_fails_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    file = files[3]
    doc = json.loads(file.read_text())
    doc["assets"][0]["category"] = "fonts_missing"
    file.write_text(json.dumps(doc), encoding="utf-8")
    _fake_renderer(monkeypatch)
    with pytest.raises(ValueError, match="passed, pinned font-audit evidence"):
        _detect(tmp_path, files)
    assert not (tmp_path / "duplicate-receipt.json").exists()


def test_failed_font_audit_receipt_cannot_start_duplicate_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    doc = json.loads(files[4].read_text())
    doc["status"] = "failed"
    files[4].write_text(json.dumps(doc), encoding="utf-8")
    _fake_renderer(monkeypatch)
    with pytest.raises(ValueError, match="passed, pinned font-audit evidence"):
        _detect(tmp_path, files)


def test_modified_effective_member_rejected_before_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    doc = json.loads(files[2].read_text())
    doc["assets"][0]["sha256"] = "0" * 64
    files[2].write_text(json.dumps(doc), encoding="utf-8")
    _fake_renderer(monkeypatch)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _detect(tmp_path, files)
    assert not (tmp_path / "duplicate-reports").exists()


def test_duplicate_reports_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    _fake_renderer(monkeypatch)
    first = _detect(tmp_path, files)
    with pytest.raises(FileExistsError, match="does not overwrite"):
        _detect(tmp_path, files)
    assert first.receipt.is_file()


def test_missing_font_classification_rejected_even_with_passed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _inputs(tmp_path, monkeypatch)
    # Retain matching hashes for this forged report/receipt, but remove a row.
    report = json.loads(files[3].read_text())
    report["assets"].pop()
    files[3].write_text(json.dumps(report), encoding="utf-8")
    receipt = json.loads(files[4].read_text())
    receipt["report"]["sha256"] = sha256_file(files[3])
    files[4].write_text(json.dumps(receipt), encoding="utf-8")
    _fake_renderer(monkeypatch)
    with pytest.raises(ValueError, match="classifications differ"):
        _detect(tmp_path, files)
