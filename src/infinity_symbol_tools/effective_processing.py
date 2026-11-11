"""Build a verifiable *override-stage* symbol artifact from pinned raw evidence.

This stage applies reviewed upstream-identity overrides and deliberately stops
before font conversion, deduplication and compression. It cannot be published
as a final browser-symbol result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from infinity_symbol_tools.override_processing import (
    _checked_svg,
    _portable_path,
    resolve_reviewed_overrides,
)
from infinity_symbol_tools.raw_acquisition import (
    ASSET_ROOT_URL,
    REPORT_FORMAT as RAW_REPORT_FORMAT,
    REPORT_VERSION as RAW_REPORT_VERSION,
)
from infinity_symbol_tools.snapshot_archive import create_timestamped_archive
from infinity_symbol_tools.snapshot_provenance import (
    load_snapshot_manifest,
    sha256_file,
    write_snapshot_manifest,
)
from infinity_symbol_tools.source_plan import load_source_plan

LOOKUP_FORMAT = "InfinityDB symbol override lookup plan"
LOOKUP_VERSION = 1
EFFECTIVE_FORMAT = "InfinityDB effective symbol processing report"
EFFECTIVE_VERSION = 1


class EffectiveProcessingError(ValueError):
    """The pinned inputs or effective-symbol processing are invalid."""


class EffectiveResult(NamedTuple):
    archive: Path
    manifest: Path
    report: Path
    asset_count: int
    override_count: int


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _exact(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise EffectiveProcessingError(f"{context} requires exactly {sorted(fields)}")
    return value


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EffectiveProcessingError(f"Cannot load {path}: {exc}") from exc


def load_lookup_plan(
    lookups_path: Path, source_plan: dict[str, Any],
) -> dict[str, str]:
    """Pin override lookup paths without inferring them from ZIP member names."""
    document = _exact(
        _load_json(lookups_path),
        {"format", "formatVersion", "sourcePlanSha256", "lookups"},
        "override lookup plan",
    )
    if document["format"] != LOOKUP_FORMAT or document["formatVersion"] != LOOKUP_VERSION:
        raise EffectiveProcessingError("Unknown override lookup format/version")
    if document["sourcePlanSha256"] != hashlib.sha256(_canonical(source_plan)).hexdigest():
        raise EffectiveProcessingError("Override lookup source plan SHA-256 mismatch")
    rows = document["lookups"]
    if not isinstance(rows, list):
        raise EffectiveProcessingError("Override lookups must be an array")
    mapping: dict[str, str] = {}
    paths: set[str] = set()
    for index, value in enumerate(rows):
        row = _exact(value, {"url", "overridePath"}, f"override lookup {index}")
        url, relative = row["url"], row["overridePath"]
        if not isinstance(url, str) or url in mapping or not isinstance(relative, str):
            raise EffectiveProcessingError(f"Duplicate or invalid override lookup {index}")
        _portable_path(relative)
        folded = relative.casefold()
        if folded in paths:
            raise EffectiveProcessingError(f"Override lookup path collision: {relative}")
        paths.add(folded)
        mapping[url] = relative
    source_urls = [row["url"] for row in source_plan["requests"]]
    if list(mapping) != source_urls:
        raise EffectiveProcessingError("Override lookup URLs differ from source plan")
    return mapping


def read_pinned_raw_source(
    plan: dict[str, Any],
    army_archive: Path,
    raw_archive: Path,
    raw_manifest: Path,
    raw_report: Path,
) -> dict[str, bytes | None]:
    """Validate the entire raw archive, v3 provenance and URL outcome coverage."""
    if plan["inputArtifact"] != {
        "name": army_archive.name, "sha256": sha256_file(army_archive),
    }:
        raise EffectiveProcessingError("Raw source Army identity does not match input")
    provenance = load_snapshot_manifest(raw_manifest, archive=raw_archive)
    if (
        provenance["formatVersion"] != 3
        or provenance["snapshot"]["type"] != "symbols"
        or provenance["source"]["url"] != ASSET_ROOT_URL
        or provenance.get("inputArtifact", {}).get("name") != army_archive.name
        or provenance.get("inputArtifact", {}).get("sha256")
        != plan["inputArtifact"]["sha256"]
    ):
        raise EffectiveProcessingError("Raw snapshot provenance is not bound to Army source")

    report = _exact(
        _load_json(raw_report),
        {
            "format", "formatVersion", "inputArtifact", "sourcePlanSha256",
            "snapshot", "assets", "unavailableAssets",
        },
        "raw acquisition report",
    )
    if report["format"] != RAW_REPORT_FORMAT or report["formatVersion"] != RAW_REPORT_VERSION:
        raise EffectiveProcessingError("Unexpected raw acquisition report version")
    if (
        report["inputArtifact"] != plan["inputArtifact"]
        or report["sourcePlanSha256"] != hashlib.sha256(_canonical(plan)).hexdigest()
    ):
        raise EffectiveProcessingError("Raw report source-plan/Army identity mismatch")
    snapshot = _exact(
        report["snapshot"],
        {
            "archiveName", "sha256", "snapshotManifestName",
            "snapshotManifestSha256", "documentCount", "acquiredAt",
        },
        "raw report snapshot",
    )
    if (
        snapshot["archiveName"] != raw_archive.name
        or snapshot["sha256"] != sha256_file(raw_archive)
        or snapshot["snapshotManifestName"] != raw_manifest.name
        or snapshot["snapshotManifestSha256"] != sha256_file(raw_manifest)
        or snapshot["acquiredAt"] != provenance["snapshot"]["acquiredAt"]
    ):
        raise EffectiveProcessingError("Raw report archive/provenance identity mismatch")

    requested = {row["url"]: row["archivePath"] for row in plan["requests"]}
    covered: dict[str, bytes | None] = {}
    members: dict[str, bytes] = {}
    if not isinstance(report["assets"], list) or not isinstance(
        report["unavailableAssets"], list
    ):
        raise EffectiveProcessingError("Raw source outcomes must be arrays")
    with zipfile.ZipFile(raw_archive) as archive:
        filenames = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(set(filenames)) != len(filenames):
            raise EffectiveProcessingError("Duplicate raw ZIP members")
        for index, value in enumerate(report["assets"]):
            row = _exact(
                value, {"url", "archivePath", "sha256", "sourceMethod"},
                f"raw asset {index}",
            )
            url = row["url"]
            if (
                not isinstance(url, str)
                or url not in requested
                or url in covered
                or row["archivePath"] != requested[url]
                or row["sourceMethod"] != "network"
                or not isinstance(row["sha256"], str)
            ):
                raise EffectiveProcessingError(f"Unapproved raw asset {index}")
            try:
                body = archive.read(row["archivePath"])
            except KeyError as exc:
                raise EffectiveProcessingError(
                    f"Missing raw ZIP member: {row['archivePath']}"
                ) from exc
            if hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise EffectiveProcessingError(f"Raw SVG hash mismatch: {url}")
            _checked_svg(body, source=f"raw input {url}")
            covered[url] = body
            members[row["archivePath"]] = body
    for index, value in enumerate(report["unavailableAssets"]):
        row = _exact(
            value, {"url", "archivePath", "httpStatus"}, f"unavailable source {index}",
        )
        url = row["url"]
        if (
            not isinstance(url, str)
            or url not in requested
            or url in covered
            or row["archivePath"] != requested[url]
            or row["httpStatus"] != 404
        ):
            raise EffectiveProcessingError(f"Unapproved missing raw source {index}")
        covered[url] = None
    if (
        set(covered) != set(requested)
        or set(filenames) != set(members)
        or snapshot["documentCount"] != len(members)
        or provenance["snapshot"]["documentCount"] != len(members)
    ):
        raise EffectiveProcessingError("Raw source coverage or ZIP member count mismatch")
    return covered


def _next_artifact_paths(
    destination: Path, manifest_directory: Path, now: datetime,
) -> tuple[Path, Path, Path]:
    stem = "EFFECTIVE-SYMBOLS " + now.strftime("%Y%m%d-%H%M%S")
    index = 1
    while True:
        base = stem if index == 1 else f"{stem}-{index}"
        archive = destination / f"{base}.zip"
        manifest = manifest_directory / f"{base}.json"
        report = destination / f"{base}.effective-report.json"
        if not any(p.exists() for p in (archive, manifest, report)):
            return archive, manifest, report
        index += 1


def apply_pinned_overrides(
    source_plan_path: Path,
    lookup_plan_path: Path,
    army_archive: Path,
    raw_archive: Path,
    raw_manifest: Path,
    raw_report: Path,
    override_root: Path,
    destination: Path,
    manifest_directory: Path,
    *,
    processed_at: datetime,
    project_root: Path | None = None,
) -> EffectiveResult:
    """Build an override-stage ZIP and report without modifying raw artifacts."""
    if processed_at.tzinfo is None or processed_at.utcoffset() is None:
        raise EffectiveProcessingError("processed_at must be timezone-aware")
    plan = load_source_plan(source_plan_path, army_archive=army_archive)
    lookups = load_lookup_plan(lookup_plan_path, plan)
    upstream = read_pinned_raw_source(
        plan, army_archive, raw_archive, raw_manifest, raw_report,
    )
    resolved = resolve_reviewed_overrides(upstream, lookups, override_root)
    destination.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    rows: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    source_unavailable = [
        {"url": url, "archivePath": row["archivePath"], "httpStatus": 404}
        for row in plan["requests"]
        if (url := row["url"]) in upstream and upstream[url] is None
    ]
    with tempfile.TemporaryDirectory(
        dir=destination, prefix=".effective-symbols-",
    ) as staging:
        work = Path(staging)
        root = work / "effective"
        for source in plan["requests"]:
            url, archive_path = source["url"], source["archivePath"]
            symbol = resolved.symbols[url]
            if symbol.body is None:
                unavailable.append({
                    "url": url, "archivePath": archive_path, "httpStatus": 404,
                })
                continue
            target = root / archive_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(symbol.body)
            files.append(target)
            rows.append({
                "url": url,
                "archivePath": archive_path,
                "sourceMethod": symbol.source_method,
                "upstreamSha256": symbol.upstream_sha256,
                "sha256": symbol.effective_sha256,
                "overridePath": symbol.override_path,
                "overrideSha256": (
                    symbol.effective_sha256 if symbol.override_path is not None else None
                ),
            })
        if not files:
            raise EffectiveProcessingError("No effective SVG assets can be published")

        # Detect source/override mutation between validation and final archive creation.
        if plan["inputArtifact"]["sha256"] != sha256_file(army_archive):
            raise EffectiveProcessingError("Army source changed during override processing")
        if resolved != resolve_reviewed_overrides(upstream, lookups, override_root):
            raise EffectiveProcessingError("Reviewed override files changed during processing")
        staged = create_timestamped_archive(
            files, work / "archives", prefix="EFFECTIVE-SYMBOLS",
            root=root, now=processed_at,
        )
        archive, manifest, report = _next_artifact_paths(
            destination, manifest_directory, processed_at,
        )
        published: list[Path] = []
        try:
            with staged.open("rb") as source, archive.open("xb") as output:
                try:
                    shutil.copyfileobj(source, output)
                except BaseException:
                    output.close()
                    archive.unlink(missing_ok=True)
                    raise
            published.append(archive)
            created = write_snapshot_manifest(
                archive, manifest_directory,
                snapshot_type="symbols", acquired_at=processed_at,
                source_url=ASSET_ROOT_URL, document_count=len(files),
                project_root=project_root or Path.cwd(), input_artifact=army_archive,
            )
            if created != manifest:
                raise EffectiveProcessingError("Unexpected effective snapshot manifest path")
            published.append(manifest)
            load_snapshot_manifest(manifest, archive=archive)
            report_data = {
                "format": EFFECTIVE_FORMAT,
                "formatVersion": EFFECTIVE_VERSION,
                "processingStages": {
                    "overrides": "completed",
                    "fontAudit": "pending",
                    "deduplication": "pending",
                    "textConversion": "pending",
                    "compression": "pending",
                },
                "inputArtifact": dict(plan["inputArtifact"]),
                "sourcePlanSha256": hashlib.sha256(_canonical(plan)).hexdigest(),
                "overrideLookupPlanSha256": hashlib.sha256(
                    _canonical(_load_json(lookup_plan_path))
                ).hexdigest(),
                "rawSourceReportSha256": sha256_file(raw_report),
                "rawSnapshot": {
                    "archiveName": raw_archive.name,
                    "sha256": sha256_file(raw_archive),
                    "manifestName": raw_manifest.name,
                    "manifestSha256": sha256_file(raw_manifest),
                },
                "snapshot": {
                    "archiveName": archive.name,
                    "sha256": sha256_file(archive),
                    "manifestName": manifest.name,
                    "manifestSha256": sha256_file(manifest),
                    "documentCount": len(files),
                    "processedAt": processed_at.isoformat(timespec="seconds"),
                },
                "assets": rows,
                "unavailableAssets": unavailable,
                "rawUnavailableAssets": source_unavailable,
                "unusedOverrides": list(resolved.unused_overrides),
                "overrideSummary": {
                    "direct": resolved.direct_override_count,
                    "propagated": resolved.propagated_override_count,
                },
            }
            with report.open("xb") as stream:
                published.append(report)
                stream.write(_canonical(report_data))
        except BaseException:
            for file in reversed(published):
                file.unlink(missing_ok=True)
            raise
    return EffectiveResult(
        archive, manifest, report, len(rows),
        resolved.direct_override_count + resolved.propagated_override_count,
    )



def main(argv: list[str] | None = None) -> int:
    """Apply only reviewed SVG overrides, never the later processing stages."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-plan", type=Path, required=True)
    parser.add_argument("--override-lookups", type=Path, required=True)
    parser.add_argument("--army", type=Path, required=True)
    parser.add_argument("--raw-archive", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--raw-report", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = apply_pinned_overrides(
            args.source_plan,
            args.override_lookups,
            args.army,
            args.raw_archive,
            args.raw_manifest,
            args.raw_report,
            args.overrides,
            args.destination,
            args.manifest_dir,
            processed_at=datetime.now().astimezone(),
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"Verified override-stage artifact -> {result.archive} "
        f"({result.asset_count} SVGs, {result.override_count} overrides); "
        f"provenance: {result.manifest}; report: {result.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
