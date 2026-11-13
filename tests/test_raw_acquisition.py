"""Offline-only verification of raw SVG acquisition and source handoff."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError

import pytest

from infinity_symbol_tools import raw_acquisition
from infinity_symbol_tools.cli import main
from infinity_symbol_tools.raw_acquisition import (
    ASSET_ROOT_URL,
    REPORT_FORMAT,
    REPORT_VERSION,
    acquire_raw_symbols,
)
from infinity_symbol_tools.snapshot_archive import create_timestamped_archive
from infinity_symbol_tools.snapshot_provenance import (
    load_snapshot_manifest,
    sha256_file,
    snapshot_content_sha256,
)
from infinity_symbol_tools.source_plan import (
    FORMAT,
    VERSION,
    SourcePlanError,
)

STAMP = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
CUBE_URL = ASSET_ROOT_URL + "icon/cube.svg"
UNIT_URL = ASSET_ROOT_URL + "logo/units/unit.svg"
CUBE_SVG = b'<?xml version="1.0" encoding="utf-8"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="1"/></svg>'
UNIT_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="2"/></svg>'


class Response:
    def __init__(self, url: str, body: bytes, *, final_url: str | None = None):
        self.url = url
        self.body = body
        self.final_url = final_url or url

    def geturl(self) -> str:
        return self.final_url

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


def _plan(army: Path) -> dict:
    return {
        "format": FORMAT,
        "formatVersion": VERSION,
        "inputArtifact": {
            "name": army.name,
            "sha256": sha256_file(army),
        },
        "sourceDocumentCount": 2,
        "requests": [
            {"url": CUBE_URL, "archivePath": "static/characteristics/cube.svg"},
            {"url": UNIT_URL, "archivePath": "units/unit.svg"},
        ],
    }


def _setup(tmp_path: Path) -> tuple[Path, dict]:
    army = tmp_path / "JSON 20260917-120000.zip"
    with zipfile.ZipFile(army, "w") as data:
        data.writestr("metadata.json", "{}")
        data.writestr("101-panoceania.json", '{"version":"7.26246.158","units":[]}')
    return army, _plan(army)


def _opener(url: str, *, timeout: int):
    assert timeout == 30
    return Response(url, {CUBE_URL: CUBE_SVG, UNIT_URL: UNIT_SVG}[url])


@pytest.mark.parametrize("collision", ["identical-manifest", "different-manifest", "archive"])
def test_manifest_race_preserves_other_acquisition_output(
    tmp_path: Path, monkeypatch, collision: str,
) -> None:
    from infinity_symbol_tools import snapshot_provenance as provenance

    army, plan = _setup(tmp_path)
    link = provenance.os.link
    winner = b""
    target = None

    def competing_link(source: Path, destination: Path) -> None:
        nonlocal winner, target
        target = destination
        winner = source.read_bytes() if collision == "identical-manifest" else b"other acquisition\n"
        destination.write_bytes(winner)
        link(source, destination)

    if collision == "archive":
        original_publish = raw_acquisition._publish_copy

        def competing_copy(source, destination):
            nonlocal target, winner
            target = destination
            winner = b"other acquisition's source ZIP"
            destination.write_bytes(winner)
            return original_publish(source, destination)

        monkeypatch.setattr(raw_acquisition, "_publish_copy", competing_copy)
    else:
        monkeypatch.setattr(provenance.os, "link", competing_link)
    with pytest.raises((provenance.SnapshotProvenanceError, FileExistsError)):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=_opener, delay=0, project_root=tmp_path,
        )
    assert target is not None and target.read_bytes() == winner
    assert list((tmp_path / "symbols").glob("*.zip")) == ([target] if collision == "archive" else [])
    assert not list((tmp_path / "symbols").glob("*.raw-report.json"))
    assert not list((tmp_path / "manifests").glob("*.tmp"))


def test_pinned_raw_acquisition_emits_source_bytes_and_v3_provenance(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)
    delays: list[float] = []
    result = acquire_raw_symbols(
        plan, army, tmp_path / "symbols", tmp_path / "manifests",
        acquired_at=STAMP, opener=_opener, delay=0.125,
        sleeper=delays.append, project_root=tmp_path,
    )
    assert (result.downloaded, result.unavailable) == (2, 0)
    assert result.archive.name == "SYMBOLS 20261111-120000.zip"
    assert delays == [0.125]

    with zipfile.ZipFile(result.archive) as output:
        assert output.namelist() == [
            "static/characteristics/cube.svg", "units/unit.svg",
        ]
        assert output.read("static/characteristics/cube.svg") == CUBE_SVG
        assert output.read("units/unit.svg") == UNIT_SVG
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in output.infolist())

    provenance = load_snapshot_manifest(result.snapshot_manifest, archive=result.archive)
    assert provenance["formatVersion"] == 3
    assert provenance["snapshot"]["type"] == "symbols"
    assert provenance["snapshot"]["documentCount"] == 2
    assert provenance["snapshot"]["contentSha256"] == snapshot_content_sha256(result.archive)
    assert provenance["snapshot"]["archive"]["sha256"] == sha256_file(result.archive)
    assert provenance["inputArtifact"]["sha256"] == sha256_file(army)
    assert provenance["source"]["url"] == ASSET_ROOT_URL

    report = json.loads(result.report.read_text(encoding="utf-8"))
    assert report["format"] == REPORT_FORMAT
    assert report["formatVersion"] == REPORT_VERSION
    assert report["snapshot"]["sha256"] == sha256_file(result.archive)
    assert report["snapshot"]["snapshotManifestSha256"] == sha256_file(result.snapshot_manifest)
    assert report["sourcePlanSha256"] == hashlib.sha256(
        (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    ).hexdigest()
    assert [record["url"] for record in report["assets"]] == [CUBE_URL, UNIT_URL]
    assert all(record["sourceMethod"] == "network" for record in report["assets"])
    assert report["unavailableAssets"] == []
    assert result.report.read_bytes().endswith(b"\n")


def test_raw_zip_bytes_equal_existing_deterministic_archive_contract(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)
    result = acquire_raw_symbols(
        plan, army, tmp_path / "symbols", tmp_path / "manifests",
        acquired_at=STAMP, opener=_opener, delay=0, project_root=tmp_path,
    )
    root = tmp_path / "expected"
    (root / "static/characteristics").mkdir(parents=True)
    (root / "units").mkdir()
    (root / "static/characteristics/cube.svg").write_bytes(CUBE_SVG)
    (root / "units/unit.svg").write_bytes(UNIT_SVG)
    expected = create_timestamped_archive(
        [root / "units/unit.svg", root / "static/characteristics/cube.svg"],
        tmp_path / "reference", prefix="SYMBOLS", root=root, now=STAMP,
    )
    assert result.archive.read_bytes() == expected.read_bytes()

    second = acquire_raw_symbols(
        plan, army, tmp_path / "symbols", tmp_path / "manifests",
        acquired_at=STAMP, opener=_opener, delay=0, project_root=tmp_path,
    )
    assert second.archive.name == "SYMBOLS 20261111-120000-2.zip"
    assert second.archive.read_bytes() == expected.read_bytes()
    assert second.snapshot_manifest.is_file()
    assert result.archive.read_bytes() == expected.read_bytes()


def test_http_404_is_reported_but_not_published_as_placeholder(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        if url == UNIT_URL:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return Response(url, CUBE_SVG)

    result = acquire_raw_symbols(
        plan, army, tmp_path / "symbols", tmp_path / "manifests",
        acquired_at=STAMP, opener=opener, delay=0, project_root=tmp_path,
    )
    assert (result.downloaded, result.unavailable) == (1, 1)
    with zipfile.ZipFile(result.archive) as output:
        assert output.namelist() == ["static/characteristics/cube.svg"]
    manifest = load_snapshot_manifest(result.snapshot_manifest, archive=result.archive)
    assert manifest["snapshot"]["documentCount"] == 1
    report = json.loads(result.report.read_text())
    assert report["unavailableAssets"] == [{
        "url": UNIT_URL, "archivePath": "units/unit.svg", "httpStatus": 404,
    }]


@pytest.mark.parametrize("status", [403, 429, 500])
def test_non_404_errors_publish_nothing(tmp_path: Path, status: int) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        if url == UNIT_URL:
            raise HTTPError(url, status, "Upstream error", {}, None)
        return Response(url, CUBE_SVG)

    with pytest.raises(HTTPError) as error:
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=opener, delay=0,
        )
    assert error.value.code == status
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []
    assert not (tmp_path / "manifests").exists()


@pytest.mark.parametrize("body", [b"not xml", b"<html></html>", b"<svg>"])
def test_invalid_svg_aborts_without_publication(tmp_path: Path, body: bytes) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        return Response(url, body if url == UNIT_URL else CUBE_SVG)

    with pytest.raises(SourcePlanError, match="Invalid SVG"):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=opener, delay=0,
        )
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []


def test_redirect_to_unpinned_url_fails(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        return Response(url, CUBE_SVG, final_url=url + "?redirected=1")

    with pytest.raises(SourcePlanError, match="Redirected SVG URL"):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=opener, delay=0,
        )
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []


def test_changed_army_zip_mid_acquisition_fails_closed(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        if url == UNIT_URL:
            with army.open("ab") as output:
                output.write(b"changed")
        return Response(url, CUBE_SVG)

    with pytest.raises(SourcePlanError, match="SHA-256"):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=opener, delay=0,
        )
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []


def test_all_404_fails_closed(tmp_path: Path) -> None:
    army, plan = _setup(tmp_path)

    def opener(url: str, *, timeout: int):
        raise HTTPError(url, 404, "Not found", {}, None)

    with pytest.raises(SourcePlanError, match="All requested SVG assets"):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=opener, delay=0,
        )
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []


def test_manifest_write_failure_removes_new_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    army, plan = _setup(tmp_path)

    def fail(*_args: object, **_kwargs: object) -> Path:
        raise ValueError("Injected provenance failure")

    monkeypatch.setattr(raw_acquisition, "write_snapshot_manifest", fail)
    with pytest.raises(ValueError, match="Injected provenance failure"):
        acquire_raw_symbols(
            plan, army, tmp_path / "symbols", tmp_path / "manifests",
            acquired_at=STAMP, opener=_opener, delay=0,
        )
    assert list((tmp_path / "symbols").glob("SYMBOLS *")) == []


def test_source_validation_cli_remains_network_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    army, plan = _setup(tmp_path)
    plan_path = tmp_path / "requests.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def fail_if_acquired(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unexpected acquisition")

    monkeypatch.setattr("infinity_symbol_tools.cli.acquire_raw_symbols", fail_if_acquired)
    assert main(["--plan", str(plan_path), "--army", str(army)]) == 0
    assert "no network access" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()
