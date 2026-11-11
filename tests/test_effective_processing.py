"""Offline processed-symbol override-stage contract and source identity tests."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import pytest

from infinity_symbol_tools import effective_processing
from infinity_symbol_tools.effective_processing import (
    EFFECTIVE_FORMAT,
    EffectiveProcessingError,
    apply_pinned_overrides,
    load_lookup_plan,
)
from infinity_symbol_tools.raw_acquisition import acquire_raw_symbols
from infinity_symbol_tools.snapshot_provenance import (
    load_snapshot_manifest,
    sha256_file,
)
from infinity_symbol_tools.source_plan import FORMAT, VERSION

STAMP = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
BASE = "https://assets.corvusbelli.net/army/img/"
A = BASE + "logo/units/unit-a.svg"
B = BASE + "logo/units/unit-b.svg"
C = BASE + "icon/cube.svg"
RAW_SHARED = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="2"/></svg>'
RAW_CUBE = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1"/></svg>'
OVERRIDE = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>'


def _canonical(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


class Response:
    def __init__(self, url: str, body: bytes):
        self.url = url
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def geturl(self) -> str:
        return self.url

    def read(self) -> bytes:
        return self.body


def _prepare(
    tmp_path: Path, *, missing: set[str] | None = None,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    missing = missing or set()
    army = tmp_path / "JSON 20260917-120000.zip"
    with zipfile.ZipFile(army, "w") as archive:
        archive.writestr("metadata.json", "{}")
        archive.writestr("101-test.json", '{"version":"7.26246.158","units":[]}')
    requests = [
        {"url": C, "archivePath": "static/characteristics/cube.svg"},
        {"url": A, "archivePath": "units/unit-a.svg"},
        {"url": B, "archivePath": "units/unit-b.svg"},
    ]
    plan = {
        "format": FORMAT,
        "formatVersion": VERSION,
        "inputArtifact": {"name": army.name, "sha256": sha256_file(army)},
        "sourceDocumentCount": 2,
        "requests": requests,
    }
    plan_path = tmp_path / "source-plan.json"
    plan_path.write_bytes(_canonical(plan))
    lookups = {
        "format": "InfinityDB symbol override lookup plan",
        "formatVersion": 1,
        "sourcePlanSha256": hashlib.sha256(_canonical(plan)).hexdigest(),
        "lookups": [
            {"url": C, "overridePath": "characteristics/cube.svg"},
            {"url": A, "overridePath": "units/unit-a.svg"},
            {"url": B, "overridePath": "units/unit-b.svg"},
        ],
    }
    lookups_path = tmp_path / "lookups.json"
    lookups_path.write_bytes(_canonical(lookups))

    def opener(url: str, *, timeout: int):
        assert timeout == 30
        if url in missing:
            raise HTTPError(url, 404, "not found", {}, None)
        return Response(url, RAW_CUBE if url == C else RAW_SHARED)

    raw = acquire_raw_symbols(
        plan, army,
        tmp_path / "raw-symbols", tmp_path / "raw-manifests",
        acquired_at=STAMP, opener=opener, delay=0,
        project_root=tmp_path,
    )
    return (
        army, plan_path, lookups_path, raw.archive, raw.snapshot_manifest, raw.report,
    )


def _process(
    tmp_path: Path, paths: tuple[Path, Path, Path, Path, Path, Path],
    *,
    override_root: Path | None = None,
):
    army, plan, lookup, raw, manifest, report = paths
    return apply_pinned_overrides(
        plan, lookup, army, raw, manifest, report,
        override_root or tmp_path / "overrides",
        tmp_path / "effective", tmp_path / "effective-manifests",
        processed_at=STAMP, project_root=tmp_path,
    )


def test_processed_override_stage_keeps_raw_upstream_and_effective_hashes(
    tmp_path: Path,
) -> None:
    paths = _prepare(tmp_path)
    override_root = tmp_path / "overrides"
    (override_root / "units").mkdir(parents=True)
    (override_root / "units/unit-a.svg").write_bytes(OVERRIDE)
    result = _process(tmp_path, paths)
    assert result.asset_count == 3
    assert result.override_count == 2
    assert result.archive.name == "EFFECTIVE-SYMBOLS 20261111-120000.zip"
    assert sha256_file(result.archive) != sha256_file(paths[3])
    provenance = load_snapshot_manifest(result.manifest, archive=result.archive)
    assert provenance["formatVersion"] == 3
    assert provenance["snapshot"]["documentCount"] == 3
    assert provenance["inputArtifact"]["sha256"] == sha256_file(paths[0])

    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["format"] == EFFECTIVE_FORMAT
    assert report["formatVersion"] == 1
    assert report["processingStages"] == {
        "overrides": "completed",
        "fontAudit": "pending",
        "deduplication": "pending",
        "textConversion": "pending",
        "compression": "pending",
    }
    assert report["overrideSummary"] == {"direct": 1, "propagated": 1}
    assert report["unusedOverrides"] == []
    assert report["rawSnapshot"]["sha256"] == sha256_file(paths[3])
    assert report["sourcePlanSha256"] == hashlib.sha256(
        paths[1].read_bytes()
    ).hexdigest()
    assert report["overrideLookupPlanSha256"] == hashlib.sha256(
        paths[2].read_bytes()
    ).hexdigest()
    assert report["rawSourceReportSha256"] == sha256_file(paths[5])

    assets = {row["url"]: row for row in report["assets"]}
    for url in (A, B):
        assert assets[url]["sourceMethod"] == "override"
        assert assets[url]["sha256"] == hashlib.sha256(OVERRIDE).hexdigest()
        assert assets[url]["upstreamSha256"] == hashlib.sha256(RAW_SHARED).hexdigest()
        assert assets[url]["overridePath"] == "units/unit-a.svg"
        assert assets[url]["overrideSha256"] == hashlib.sha256(OVERRIDE).hexdigest()
    assert assets[C]["sourceMethod"] == "network"
    assert assets[C]["overridePath"] is None
    with zipfile.ZipFile(result.archive) as archive:
        assert archive.read("units/unit-a.svg") == OVERRIDE
        assert archive.read("units/unit-b.svg") == OVERRIDE
        assert archive.read("static/characteristics/cube.svg") == RAW_CUBE


def test_raw_404_can_be_replaced_by_direct_override(tmp_path: Path) -> None:
    paths = _prepare(tmp_path, missing={A})
    root = tmp_path / "overrides"
    (root / "units").mkdir(parents=True)
    (root / "units/unit-a.svg").write_bytes(OVERRIDE)
    result = _process(tmp_path, paths)
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["unavailableAssets"] == []
    assert report["rawUnavailableAssets"] == [
        {"url": A, "archivePath": "units/unit-a.svg", "httpStatus": 404},
    ]
    row = next(asset for asset in report["assets"] if asset["url"] == A)
    assert row["sourceMethod"] == "override"
    assert row["upstreamSha256"] is None


def test_missing_raw_without_override_remains_unavailable(tmp_path: Path) -> None:
    paths = _prepare(tmp_path, missing={B})
    result = _process(tmp_path, paths)
    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert result.asset_count == 2
    assert report["unavailableAssets"] == [
        {"url": B, "archivePath": "units/unit-b.svg", "httpStatus": 404},
    ]
    assert report["rawUnavailableAssets"] == report["unavailableAssets"]


def test_source_plan_mutation_rejects_override_lookups(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    lookup = json.loads(paths[2].read_text(encoding="utf-8"))
    lookup["sourcePlanSha256"] = "0" * 64
    paths[2].write_bytes(_canonical(lookup))
    with pytest.raises(EffectiveProcessingError, match="source plan SHA-256"):
        _process(tmp_path, paths)


def test_raw_report_mutation_is_rejected(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    report = json.loads(paths[5].read_text(encoding="utf-8"))
    report["assets"][0]["sha256"] = "0" * 64
    paths[5].write_bytes(_canonical(report))
    with pytest.raises(EffectiveProcessingError, match="Raw SVG hash mismatch"):
        _process(tmp_path, paths)
    assert not (tmp_path / "effective").exists()


def test_lookup_path_collisions_are_rejected(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    lookup = json.loads(paths[2].read_text(encoding="utf-8"))
    lookup["lookups"][2]["overridePath"] = "UNITS/UNIT-A.svg"
    paths[2].write_bytes(_canonical(lookup))
    with pytest.raises(EffectiveProcessingError, match="path collision"):
        _process(tmp_path, paths)


def test_existing_artifact_is_never_overwritten(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    first = _process(tmp_path, paths)
    second = _process(tmp_path, paths)
    assert first.archive.read_bytes() == second.archive.read_bytes()
    assert second.archive.name == "EFFECTIVE-SYMBOLS 20261111-120000-2.zip"


def test_failed_manifest_generation_removes_new_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _prepare(tmp_path)

    def fail(*_args: object, **_kwargs: object):
        raise ValueError("injected provenance failure")

    monkeypatch.setattr(effective_processing, "write_snapshot_manifest", fail)
    with pytest.raises(ValueError, match="injected provenance failure"):
        _process(tmp_path, paths)
    assert list((tmp_path / "effective").glob("EFFECTIVE-SYMBOLS *")) == []


def test_stale_override_source_mapping_is_rejected(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    lookup = json.loads(paths[2].read_text(encoding="utf-8"))
    lookup["lookups"].pop()
    paths[2].write_bytes(_canonical(lookup))
    with pytest.raises(EffectiveProcessingError, match="URLs differ"):
        _process(tmp_path, paths)


def test_lookup_plan_has_exact_schema(tmp_path: Path) -> None:
    paths = _prepare(tmp_path)
    source_plan = json.loads(paths[1].read_text(encoding="utf-8"))
    mapping = load_lookup_plan(paths[2], source_plan)
    assert mapping[C] == "characteristics/cube.svg"
    lookup = json.loads(paths[2].read_text(encoding="utf-8"))
    lookup["allowUnknownSources"] = True
    paths[2].write_bytes(_canonical(lookup))
    with pytest.raises(EffectiveProcessingError, match="requires exactly"):
        load_lookup_plan(paths[2], source_plan)
