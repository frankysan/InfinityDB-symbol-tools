"""Installed-font audit parity and pinned preflight evidence tests."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infinity_symbol_tools import svg_font_audit
from infinity_symbol_tools.snapshot_archive import create_timestamped_archive
from infinity_symbol_tools.snapshot_provenance import sha256_file, write_snapshot_manifest
from infinity_symbol_tools.svg_font_audit import (
    FONT_AUDIT_FORMAT,
    audit_effective_symbol_fonts,
)
from infinity_symbol_tools.svg_preflight import scan_effective_symbol_preflight

STAMP = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
GENERIC = b'<svg xmlns="http://www.w3.org/2000/svg"><text font-family="sans-serif">X</text></svg>'
UNKNOWN = (
    b'<svg xmlns="http://www.w3.org/2000/svg">'
    b'<text font-family="InfinityDBTestDefinitelyMissing_1978">X</text></svg>'
)
NO_TEXT = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


def _canonical(document: dict) -> bytes:
    return (
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _fixture(
    tmp_path: Path,
    members: dict[str, bytes] | None = None,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    members = members or {"units/a.svg": GENERIC, "units/b.svg": NO_TEXT}
    army = tmp_path / "JSON 20260917-120000.zip"
    with zipfile.ZipFile(army, "w") as output:
        output.writestr("metadata.json", "{}")
        output.writestr("101-army.json", '{"version":"7.26246.158","units":[]}')
    raw_root = tmp_path / "effective-root"
    files: list[Path] = []
    for name, content in members.items():
        file = raw_root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        files.append(file)
    archive = create_timestamped_archive(
        files, tmp_path / "archives", prefix="EFFECTIVE-SYMBOLS",
        root=raw_root, now=STAMP,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests",
        snapshot_type="symbols", acquired_at=STAMP,
        source_url="https://assets.corvusbelli.net/army/img/",
        document_count=len(members), input_artifact=army,
        project_root=tmp_path,
    )
    report = tmp_path / "effective-report.json"
    report.write_bytes(_canonical({
        "format": "InfinityDB effective symbol processing report",
        "formatVersion": 1,
        "processingStages": {
            "overrides": "completed",
            "fontAudit": "pending",
            "deduplication": "pending",
            "textConversion": "pending",
            "compression": "pending",
        },
        "snapshot": {
            "archiveName": archive.name,
            "sha256": sha256_file(archive),
            "manifestName": manifest.name,
            "manifestSha256": sha256_file(manifest),
            "documentCount": len(members),
            "processedAt": STAMP.isoformat(timespec="seconds"),
        },
        "assets": [
            {"archivePath": name, "sha256": hashlib.sha256(body).hexdigest()}
            for name, body in sorted(members.items())
        ],
    }))
    preflight = scan_effective_symbol_preflight(
        archive, manifest, report,
        tmp_path / "svg-preflight.json", tmp_path / "svg-preflight-state.json",
    )
    assert preflight.status == "passed"
    aliases = tmp_path / "aliases.json"
    aliases.write_bytes(_canonical({"schemaVersion": 1, "overrides": []}))
    return archive, manifest, report, preflight.report, preflight.receipt, aliases


def _audit(
    tmp_path: Path,
    files: tuple[Path, Path, Path, Path, Path, Path],
):
    archive, manifest, effective_report, preflight_report, preflight_receipt, aliases = files
    return audit_effective_symbol_fonts(
        archive, manifest, effective_report, preflight_report, preflight_receipt,
        tmp_path / "font-audit.json", tmp_path / "font-audit-state.json",
        alias_config=aliases,
    )


def test_font_audit_matches_legacy_generic_font_and_no_text_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _fixture(tmp_path)
    monkeypatch.setattr(
        svg_font_audit.svg_processor, "load_font_index",
        lambda: ({}, {}, 0, 0),
    )
    result = _audit(tmp_path, files)
    assert result.status == "passed"
    assert result.summary == {
        "svgCount": 2,
        "fontAvailableAssetCount": 1,
        "fontMissingAssetCount": 0,
        "noActiveTextAssetCount": 1,
        "implicitDefaultAssetCount": 0,
        "effectiveFontReferenceCount": 1,
        "availableFontReferenceCount": 0,
        "missingFontReferenceCount": 0,
        "ambiguousFontReferenceCount": 0,
        "genericFontReferenceCount": 1,
        "normalizedAliasReferenceCount": 0,
        "unusedDeclarationCount": 0,
    }
    document = json.loads(result.report.read_text(encoding="utf-8"))
    assert document["format"] == FONT_AUDIT_FORMAT
    assert document["formatVersion"] == 1
    assert document["symbolArtifact"] == {
        "name": files[0].name, "sha256": sha256_file(files[0]),
    }
    assert document["fontAliases"] == {
        "name": files[5].name, "sha256": sha256_file(files[5]),
    }
    assert document["fonts"][0]["reference"] == "sans-serif"
    assert document["fonts"][0]["status"] == "GENERIC"
    assert [row["category"] for row in document["assets"]] == [
        "fonts_available", "no_active_text",
    ]
    receipt = json.loads(result.receipt.read_text())
    assert receipt["status"] == "passed"
    assert receipt["stage"] == "fontAudit"
    assert receipt["preflightReport"] == {
        "name": files[3].name, "sha256": sha256_file(files[3]),
    }
    assert receipt["preflightReceipt"] == {
        "name": files[4].name, "sha256": sha256_file(files[4]),
    }
    assert receipt["resolvedFontFiles"] == []
    assert receipt["report"]["sha256"] == sha256_file(result.report)


def test_missing_font_records_failed_state_without_replacing_svg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _fixture(tmp_path, {"units/missing.svg": UNKNOWN})
    monkeypatch.setattr(svg_font_audit.svg_processor, "load_font_index", lambda: ({}, {}, 0, 0))
    before = files[0].read_bytes()
    result = _audit(tmp_path, files)
    assert result.status == "failed"
    assert result.summary["fontMissingAssetCount"] == 1
    assert json.loads(result.receipt.read_text())["status"] == "failed"
    assert files[0].read_bytes() == before


def test_changed_preflight_receipt_fails_closed(tmp_path: Path) -> None:
    files = _fixture(tmp_path)
    receipt = json.loads(files[4].read_text())
    receipt["status"] = "failed"
    files[4].write_bytes(_canonical(receipt))
    with pytest.raises(ValueError, match="passed, source-pinned"):
        _audit(tmp_path, files)
    assert not (tmp_path / "font-audit.json").exists()


def test_changed_preflight_report_fails_closed(tmp_path: Path) -> None:
    files = _fixture(tmp_path)
    report = json.loads(files[3].read_text())
    report["summary"]["svgCount"] = 999
    files[3].write_bytes(_canonical(report))
    with pytest.raises(ValueError, match="passed, source-pinned"):
        _audit(tmp_path, files)


def test_resolved_font_bytes_are_pinned_without_bundling_fonts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _fixture(tmp_path, {"units/text.svg": UNKNOWN})
    path = tmp_path / "custom-font.ttf"
    path.write_bytes(b"test-only fake font data; never distributed")
    monkeypatch.setattr(svg_font_audit.svg_processor, "load_font_index", lambda: ({}, {}, 1, 1))
    def fake_find_font(
        _reference, _exact_index, _compact_index, *, overrides=None,
    ):
        return {
            "status": "FOUND", "match_type": "exact",
            "matched_name": "InfinityDBTestDefinitelyMissing_1978",
            "family": "Test Font", "subfamily": "Regular",
            "full_name": "Test Font Regular", "postscript": "TestFont-Regular",
            "font_file": str(path), "normalize": "NO",
            "weight": "400", "style": "", "stretch": "",
        }
    monkeypatch.setattr(svg_font_audit.svg_processor, "find_font", fake_find_font)
    result = _audit(tmp_path, files)
    assert result.status == "passed"
    receipt = json.loads(result.receipt.read_text())
    assert receipt["resolvedFontFiles"] == [{
        "name": path.name, "sha256": sha256_file(path),
    }]
    document = json.loads(result.report.read_text())
    assert document["fonts"][0]["fontFile"] == path.name
    assert str(path) not in result.report.read_text(encoding="utf-8")


def test_font_index_failure_produces_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = _fixture(tmp_path)
    def fail():
        raise RuntimeError("mock font index unavailable")
    monkeypatch.setattr(svg_font_audit.svg_processor, "load_font_index", fail)
    with pytest.raises(RuntimeError, match="mock font index"):
        _audit(tmp_path, files)
    assert not (tmp_path / "font-audit.json").exists()


def test_font_audit_reports_are_immutable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = _fixture(tmp_path)
    monkeypatch.setattr(svg_font_audit.svg_processor, "load_font_index", lambda: ({}, {}, 0, 0))
    first = _audit(tmp_path, files)
    with pytest.raises(FileExistsError, match="does not overwrite"):
        _audit(tmp_path, files)
    assert first.report.is_file()
