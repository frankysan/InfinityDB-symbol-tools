"""Standalone SVG preflight parity with the maintained InfinityDB report format."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from infinity_symbol_tools.snapshot_archive import create_timestamped_archive
from infinity_symbol_tools.snapshot_provenance import (
    sha256_file,
    write_snapshot_manifest,
)
from infinity_symbol_tools.svg_preflight import (
    SVG_PREFLIGHT_FORMAT,
    SVG_PREFLIGHT_VERSION,
    STAGE_RECEIPT_FORMAT,
    scan_effective_symbol_preflight,
)

STAMP = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
NO_TEXT = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
WITH_TEXT = (
    b'<svg xmlns="http://www.w3.org/2000/svg">'
    b'<style>.x { font-family: "Orbitron", sans-serif; }</style>'
    b'<text style="font-family: Jura">X</text></svg>'
)


def _canonical(document: dict) -> bytes:
    return (
        json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def _effective_fixture(
    tmp_path: Path, members: dict[str, bytes],
) -> tuple[Path, Path, Path]:
    army = tmp_path / "JSON 20260917-120000.zip"
    with zipfile.ZipFile(army, "w") as target:
        target.writestr("metadata.json", "{}")
        target.writestr("101-test.json", '{"version":"7.26246.158","units":[]}')
    root = tmp_path / "effective-root"
    files: list[Path] = []
    for name, contents in members.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(contents)
        files.append(file)
    archive = create_timestamped_archive(
        files, tmp_path / "effective", prefix="EFFECTIVE-SYMBOLS",
        root=root, now=STAMP,
    )
    manifest = write_snapshot_manifest(
        archive, tmp_path / "manifests", snapshot_type="symbols",
        acquired_at=STAMP,
        source_url="https://assets.corvusbelli.net/army/img/",
        document_count=len(members), project_root=tmp_path,
        input_artifact=army,
    )
    report = {
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
    }
    report_path = tmp_path / "effective-report.json"
    report_path.write_bytes(_canonical(report))
    return archive, manifest, report_path


def _scan(
    tmp_path: Path, fixture: tuple[Path, Path, Path],
):
    archive, manifest, report = fixture
    return scan_effective_symbol_preflight(
        archive, manifest, report,
        tmp_path / "svg-preflight.json",
        tmp_path / "svg-preflight-state.json",
    )


def test_preflight_report_matches_existing_svg_preflight_contract(tmp_path: Path) -> None:
    archive, manifest, report = _effective_fixture(
        tmp_path,
        {"units/no-text.svg": NO_TEXT, "units/text.svg": WITH_TEXT},
    )
    result = _scan(tmp_path, (archive, manifest, report))
    assert result.status == "passed"
    assert result.summary == {
        "svgCount": 2,
        "parseErrorCount": 0,
        "activeTextAssetCount": 1,
        "noActiveTextAssetCount": 1,
        "fontDeclaredAssetCount": 1,
        "uniqueDeclaredFontCount": 3,
    }
    document = json.loads(result.report.read_text(encoding="utf-8"))
    assert document["format"] == SVG_PREFLIGHT_FORMAT
    assert document["formatVersion"] == SVG_PREFLIGHT_VERSION
    assert document["symbolArtifact"] == {
        "name": archive.name,
        "sha256": sha256_file(archive),
    }
    assert [row["family"] for row in document["fonts"]] == [
        "Jura", "Orbitron", "sans-serif",
    ]
    assert document["assets"] == [
        {
            "archivePath": "units/no-text.svg",
            "parseStatus": "ok",
            "activeText": False,
            "textRunCount": 0,
            "emptyTextObjectCount": 0,
            "fontFamilies": [],
        },
        {
            "archivePath": "units/text.svg",
            "parseStatus": "ok",
            "activeText": True,
            "textRunCount": 1,
            "emptyTextObjectCount": 0,
            "fontFamilies": ["Jura", "Orbitron", "sans-serif"],
        },
    ]
    receipt = json.loads(result.receipt.read_text(encoding="utf-8"))
    assert receipt == {
        "format": STAGE_RECEIPT_FORMAT,
        "formatVersion": 1,
        "stage": "svgPreflight",
        "status": "passed",
        "inputArtifact": document["symbolArtifact"],
        "report": {
            "name": result.report.name,
            "sha256": sha256_file(result.report),
        },
        "summary": dict(sorted(result.summary.items())),
    }
    assert b"\r" not in result.report.read_bytes()
    assert result.receipt.read_bytes().endswith(b"\n")


def test_preflight_records_parse_failures_without_promoting_status(tmp_path: Path) -> None:
    fixture = _effective_fixture(
        tmp_path,
        {"units/good.svg": NO_TEXT, "units/bad.svg": b"<svg>"},
    )
    result = _scan(tmp_path, fixture)
    assert result.status == "failed"
    assert result.summary["parseErrorCount"] == 1
    assert result.summary["noActiveTextAssetCount"] == 1
    document = json.loads(result.report.read_text(encoding="utf-8"))
    assert document["assets"][0]["parseStatus"] == "error"
    assert "error" in document["assets"][0]
    assert json.loads(result.receipt.read_text())["status"] == "failed"


def test_preflight_rejects_tampered_effective_archive_before_writing(
    tmp_path: Path,
) -> None:
    fixture = _effective_fixture(tmp_path, {"units/a.svg": NO_TEXT})
    with zipfile.ZipFile(fixture[0], "a") as target:
        target.writestr("units/b.svg", NO_TEXT)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _scan(tmp_path, fixture)
    assert not (tmp_path / "svg-preflight.json").exists()


def test_preflight_rejects_wrong_member_hash(tmp_path: Path) -> None:
    archive, manifest, report = _effective_fixture(
        tmp_path, {"units/a.svg": NO_TEXT},
    )
    document = json.loads(report.read_text())
    document["assets"][0]["sha256"] = "0" * 64
    report.write_bytes(_canonical(document))
    with pytest.raises(ValueError, match="member SHA-256 mismatch"):
        _scan(tmp_path, (archive, manifest, report))


def test_preflight_rejects_unapproved_extra_members(tmp_path: Path) -> None:
    archive, manifest, report = _effective_fixture(
        tmp_path, {"units/a.svg": NO_TEXT, "units/b.svg": WITH_TEXT},
    )
    document = json.loads(report.read_text())
    document["assets"].pop()
    report.write_bytes(_canonical(document))
    with pytest.raises(ValueError, match="members differ"):
        _scan(tmp_path, (archive, manifest, report))


def test_preflight_does_not_run_after_processing_stages(tmp_path: Path) -> None:
    archive, manifest, report = _effective_fixture(tmp_path, {"units/a.svg": NO_TEXT})
    document = json.loads(report.read_text())
    document["processingStages"]["textConversion"] = "completed"
    report.write_bytes(_canonical(document))
    with pytest.raises(ValueError, match="pending later stages"):
        _scan(tmp_path, (archive, manifest, report))


def test_preflight_never_overwrites_existing_report(tmp_path: Path) -> None:
    fixture = _effective_fixture(tmp_path, {"units/a.svg": NO_TEXT})
    first = _scan(tmp_path, fixture)
    with pytest.raises(FileExistsError, match="does not overwrite"):
        _scan(tmp_path, fixture)
    assert json.loads(first.receipt.read_text())["status"] == "passed"
