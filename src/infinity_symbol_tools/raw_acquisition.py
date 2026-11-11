"""Acquire unmodified upstream SVGs from a pinned, reviewed URL/path request plan.

This is raw-source acquisition only. No overrides, semantic identities, SVG
transformations, application mappings, or build publication take place here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.error import HTTPError
from urllib.request import urlopen
from xml.etree import ElementTree

from infinity_symbol_tools.snapshot_archive import create_timestamped_archive
from infinity_symbol_tools.snapshot_provenance import (
    load_snapshot_manifest,
    sha256_file,
    write_snapshot_manifest,
)
from infinity_symbol_tools.source_plan import (
    SourcePlanError,
    validate_source_plan,
)

ASSET_ROOT_URL = "https://assets.corvusbelli.net/army/img/"
REPORT_FORMAT = "InfinityDB raw symbol acquisition report"
REPORT_VERSION = 1


class RawAcquisitionResult(NamedTuple):
    archive: Path
    snapshot_manifest: Path
    report: Path
    downloaded: int
    unavailable: int


def _checked_svg(body: bytes, *, url: str) -> bytes:
    """Reject invalid/non-SVG responses without modifying source bytes."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise SourcePlanError(f"Invalid SVG from {url}: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise SourcePlanError(f"Invalid SVG from {url}: root element is not <svg>")
    return body


def _report_text(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _publish_copy(source: Path, target: Path) -> None:
    """Publish staged bytes without overwriting a preexisting artifact."""
    with source.open("rb") as read, target.open("xb") as write:
        try:
            shutil.copyfileobj(read, write)
        except BaseException:
            write.close()
            target.unlink(missing_ok=True)
            raise


def _next_names(destination: Path, manifest_directory: Path, timestamp: datetime) -> tuple[Path, Path, Path]:
    stem = "SYMBOLS " + timestamp.strftime("%Y%m%d-%H%M%S")
    number = 1
    while True:
        label = stem if number == 1 else f"{stem}-{number}"
        archive = destination / f"{label}.zip"
        manifest = manifest_directory / f"{label}.json"
        report = destination / f"{label}.raw-report.json"
        if not any(path.exists() for path in (archive, manifest, report)):
            return archive, manifest, report
        number += 1


def acquire_raw_symbols(
    plan: dict[str, Any],
    army_archive: Path,
    destination: Path,
    manifest_directory: Path,
    *,
    acquired_at: datetime | None = None,
    opener: Callable[..., Any] | None = None,
    sleeper: Callable[[float], Any] | None = None,
    delay: float = 0.2,
    project_root: Path | None = None,
) -> RawAcquisitionResult:
    """Fetch exact pinned URLs, recording 404s and publishing complete results atomically.

    A non-404 HTTP error, a redirect outside the requested URL, an invalid SVG,
    changed Army source, or a publication failure must not leave a new artifact.
    A 404 is recorded as an unavailable *source* asset, never a placeholder.
    """
    validate_source_plan(plan, army_archive=army_archive)
    if delay < 0:
        raise ValueError("delay must not be negative")
    timestamp = acquired_at or datetime.now().astimezone()
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("acquired_at must have a timezone offset")
    opener = opener or urlopen
    sleeper = sleeper or time.sleep
    project_root = project_root or Path.cwd()

    # A directory may exist after a failed run, but no published source artifacts.
    destination.mkdir(parents=True, exist_ok=True)
    requested = plan["requests"]
    assets: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    published: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".raw-symbols-", dir=destination) as staging:
        work = Path(staging)
        raw = work / "raw"
        files: list[Path] = []
        for index, request in enumerate(requested):
            url, archive_path = request["url"], request["archivePath"]
            try:
                with opener(url, timeout=30) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else url
                    if final_url != url:
                        raise SourcePlanError(
                            f"Redirected SVG URL differs from pinned request: {url} -> {final_url}"
                        )
                    body = _checked_svg(response.read(), url=url)
            except HTTPError as exc:
                if exc.code != 404:
                    raise
                missing.append({
                    "url": url,
                    "archivePath": archive_path,
                    "httpStatus": 404,
                })
            else:
                path = raw / archive_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                files.append(path)
                assets.append({
                    "url": url,
                    "archivePath": archive_path,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "sourceMethod": "network",
                })
            if index + 1 < len(requested) and delay:
                sleeper(delay)

        if not files:
            raise SourcePlanError("All requested SVG assets were unavailable")
        # An operator must not publish a plan whose pinned Army changed mid-run.
        validate_source_plan(plan, army_archive=army_archive)
        staged_archive = create_timestamped_archive(
            files, work / "archives", prefix="SYMBOLS", root=raw, now=timestamp,
        )
        archive, manifest, report = _next_names(
            destination, manifest_directory, timestamp,
        )
        try:
            _publish_copy(staged_archive, archive)
            published.append(archive)
            created = write_snapshot_manifest(
                archive,
                manifest_directory,
                snapshot_type="symbols",
                acquired_at=timestamp,
                source_url=ASSET_ROOT_URL,
                document_count=len(assets),
                project_root=project_root,
                input_artifact=army_archive,
            )
            if created != manifest:
                raise SourcePlanError("Published symbol manifest path differs from expected path")
            published.append(manifest)
            load_snapshot_manifest(manifest, archive=archive)
            source_plan_hash = hashlib.sha256(_report_text(plan)).hexdigest()
            result_document = {
                "format": REPORT_FORMAT,
                "formatVersion": REPORT_VERSION,
                "inputArtifact": dict(plan["inputArtifact"]),
                "sourcePlanSha256": source_plan_hash,
                "snapshot": {
                    "archiveName": archive.name,
                    "sha256": sha256_file(archive),
                    "snapshotManifestName": manifest.name,
                    "snapshotManifestSha256": sha256_file(manifest),
                    "documentCount": len(assets),
                    "acquiredAt": timestamp.isoformat(timespec="seconds"),
                },
                "assets": assets,
                "unavailableAssets": missing,
            }
            with report.open("xb") as target:
                published.append(report)
                target.write(_report_text(result_document))
        except BaseException:
            for path in reversed(published):
                path.unlink(missing_ok=True)
            raise

    return RawAcquisitionResult(archive, manifest, report, len(assets), len(missing))
