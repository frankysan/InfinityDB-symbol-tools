"""Versioned snapshot provenance and human annotation contracts."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SNAPSHOT_MANIFEST_FORMAT = "InfinityDB snapshot provenance"
SNAPSHOT_MANIFEST_VERSION = 3
SNAPSHOT_NOTE_FORMAT = "InfinityDB snapshot note"
SNAPSHOT_NOTE_VERSION = 1
SNAPSHOT_TYPES = frozenset({"army", "wiki", "symbols"})
_SUPPORTED_SNAPSHOT_MANIFEST_VERSIONS = frozenset({1, 2, SNAPSHOT_MANIFEST_VERSION})
_SNAPSHOT_CONTENT_HASH_HEADER = b"InfinityDB snapshot content v1\0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class SnapshotProvenanceError(ValueError):
    """Snapshot provenance or annotation data is invalid."""


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_content_sha256(archive: Path) -> str:
    """Hash normalized ZIP member paths and bytes while ignoring container metadata."""
    if not archive.is_file() or not zipfile.is_zipfile(archive):
        raise SnapshotProvenanceError(
            f"Snapshot content hash requires a ZIP archive: {archive}"
        )

    digest = hashlib.sha256(_SNAPSHOT_CONTENT_HASH_HEADER)
    with zipfile.ZipFile(archive) as source:
        members: list[tuple[str, zipfile.ZipInfo]] = []
        seen_names: set[str] = set()
        seen_casefolded: dict[str, str] = {}
        for info in source.infolist():
            if info.is_dir():
                continue
            name = _snapshot_member_name(info.filename)
            if name in seen_names:
                raise SnapshotProvenanceError(
                    f"Snapshot ZIP contains duplicate member path: {name}"
                )
            casefolded = name.casefold()
            if previous := seen_casefolded.get(casefolded):
                raise SnapshotProvenanceError(
                    "Snapshot ZIP contains case-only member collision: "
                    f"{previous!r} and {name!r}"
                )
            seen_names.add(name)
            seen_casefolded[casefolded] = name
            members.append((name, info))

        for name, info in sorted(members, key=lambda item: item[0]):
            name_bytes = name.encode("utf-8")
            member_digest = hashlib.sha256()
            member_size = 0
            with source.open(info) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    member_digest.update(chunk)
                    member_size += len(chunk)

            digest.update(len(name_bytes).to_bytes(8, "big"))
            digest.update(name_bytes)
            digest.update(member_size.to_bytes(8, "big"))
            digest.update(member_digest.digest())

    return digest.hexdigest()


def portable_project_path(path: Path, *, project_root: Path) -> str | None:
    """Return a project-relative POSIX path, or ``None`` for external files."""
    resolved = path.resolve()
    root = project_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return None


def build_snapshot_manifest(
    archive: Path,
    *,
    snapshot_type: str,
    acquired_at: datetime,
    source_url: str,
    document_count: int,
    project_root: Path,
    language: str | None = None,
    source_data_changed_on: date | None = None,
    input_artifact: Path | None = None,
) -> dict[str, Any]:
    """Create and validate one deterministic snapshot-provenance document."""
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise SnapshotProvenanceError("acquired_at must include a timezone offset")

    archive_record: dict[str, Any] = {
        "name": archive.name,
        "sha256": sha256_file(archive),
    }
    if archive_path := portable_project_path(archive, project_root=project_root):
        archive_record["path"] = archive_path

    source: dict[str, Any] = {"url": source_url}
    if language is not None:
        source["language"] = language
    if source_data_changed_on is not None:
        source["dataChangedOn"] = source_data_changed_on.isoformat()
    elif snapshot_type == "army":
        raise SnapshotProvenanceError(
            "Army snapshot provenance requires the latest encoded source data change date"
        )

    document: dict[str, Any] = {
        "format": SNAPSHOT_MANIFEST_FORMAT,
        "formatVersion": SNAPSHOT_MANIFEST_VERSION,
        "snapshot": {
            "type": snapshot_type,
            "archive": archive_record,
            "contentSha256": snapshot_content_sha256(archive),
            "acquiredAt": acquired_at.isoformat(timespec="seconds"),
            "documentCount": document_count,
        },
        "source": source,
    }

    if input_artifact is not None:
        input_record: dict[str, Any] = {
            "name": input_artifact.name,
            "sha256": sha256_file(input_artifact),
        }
        if input_path := portable_project_path(input_artifact, project_root=project_root):
            input_record["path"] = input_path
        document["inputArtifact"] = input_record

    validate_snapshot_manifest(document)
    return document


def write_snapshot_manifest(
    archive: Path,
    manifest_directory: Path,
    *,
    snapshot_type: str,
    acquired_at: datetime,
    source_url: str,
    document_count: int,
    project_root: Path,
    language: str | None = None,
    source_data_changed_on: date | None = None,
    input_artifact: Path | None = None,
) -> Path:
    """Write one immutable archive-labeled manifest as deterministic JSON."""
    document = build_snapshot_manifest(
        archive,
        snapshot_type=snapshot_type,
        acquired_at=acquired_at,
        source_url=source_url,
        document_count=document_count,
        project_root=project_root,
        language=language,
        source_data_changed_on=source_data_changed_on,
        input_artifact=input_artifact,
    )
    path = manifest_directory / f"{archive.stem}.json"
    payload = _canonical_pretty_json(document)

    manifest_directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != payload:
            raise SnapshotProvenanceError(
                f"Snapshot manifest already exists with different content: {path}"
            )
        return path

    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return path


def load_snapshot_manifest(path: Path, *, archive: Path | None = None) -> dict[str, Any]:
    """Load and validate one snapshot manifest, optionally checking its archive hashes."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotProvenanceError(f"Could not load snapshot manifest {path}: {exc}") from exc
    validate_snapshot_manifest(document, archive=archive)
    return document


def validate_snapshot_manifest(document: Any, *, archive: Path | None = None) -> None:
    """Validate current and supported legacy snapshot provenance."""
    root = _object(document, "snapshot manifest")
    _only_keys(
        root,
        {"format", "formatVersion", "snapshot", "source", "inputArtifact"},
        "snapshot manifest",
    )
    if root.get("format") != SNAPSHOT_MANIFEST_FORMAT:
        raise SnapshotProvenanceError(
            f"snapshot manifest: 'format' must be {SNAPSHOT_MANIFEST_FORMAT!r}"
        )
    format_version = root.get("formatVersion")
    if (
        type(format_version) is not int
        or format_version not in _SUPPORTED_SNAPSHOT_MANIFEST_VERSIONS
    ):
        raise SnapshotProvenanceError(
            f"snapshot manifest: unsupported formatVersion {format_version!r}"
        )

    snapshot = _object(root.get("snapshot"), "snapshot manifest.snapshot")
    snapshot_keys = {"type", "archive", "acquiredAt", "documentCount"}
    if format_version >= 2:
        snapshot_keys.add("contentSha256")
    _only_keys(snapshot, snapshot_keys, "snapshot manifest.snapshot")
    snapshot_type = _string(snapshot.get("type"), "snapshot manifest.snapshot.type")
    if snapshot_type not in SNAPSHOT_TYPES:
        choices = ", ".join(sorted(SNAPSHOT_TYPES))
        raise SnapshotProvenanceError(
            f"snapshot manifest.snapshot.type must be one of: {choices}"
        )
    acquired_at = _string(snapshot.get("acquiredAt"), "snapshot manifest.snapshot.acquiredAt")
    _aware_datetime(acquired_at, "snapshot manifest.snapshot.acquiredAt")
    document_count = snapshot.get("documentCount")
    if type(document_count) is not int or document_count < 0:
        raise SnapshotProvenanceError(
            "snapshot manifest.snapshot.documentCount must be a non-negative integer"
        )
    content_sha256 = None
    if format_version >= 2:
        content_sha256 = _sha256(
            snapshot.get("contentSha256"), "snapshot manifest.snapshot.contentSha256"
        )

    archive_record = _artifact_record(
        snapshot.get("archive"), "snapshot manifest.snapshot.archive"
    )

    source = _object(root.get("source"), "snapshot manifest.source")
    source_keys = {"url", "language"}
    if format_version >= 3:
        source_keys.add("dataChangedOn")
    _only_keys(source, source_keys, "snapshot manifest.source")
    _string(source.get("url"), "snapshot manifest.source.url")
    if "language" in source:
        _string(source["language"], "snapshot manifest.source.language")
    if format_version >= 3 and snapshot_type == "army":
        _iso_date(
            source.get("dataChangedOn"),
            "snapshot manifest.source.dataChangedOn",
        )
    elif "dataChangedOn" in source:
        _iso_date(source["dataChangedOn"], "snapshot manifest.source.dataChangedOn")

    if "inputArtifact" in root:
        _artifact_record(root["inputArtifact"], "snapshot manifest.inputArtifact")

    if archive is not None:
        actual_sha256 = sha256_file(archive)
        if actual_sha256 != archive_record["sha256"]:
            raise SnapshotProvenanceError(
                "Snapshot archive SHA-256 mismatch: "
                f"expected {archive_record['sha256']}, got {actual_sha256}"
            )
        if content_sha256 is not None:
            actual_content_sha256 = snapshot_content_sha256(archive)
            if actual_content_sha256 != content_sha256:
                raise SnapshotProvenanceError(
                    "Snapshot content SHA-256 mismatch: "
                    f"expected {content_sha256}, got {actual_content_sha256}"
                )


def validate_snapshot_note(
    document: Any,
    *,
    snapshot_sha256: str | None = None,
) -> None:
    """Validate one human-authored snapshot-note document."""
    root = _object(document, "snapshot note")
    _only_keys(
        root,
        {
            "format",
            "formatVersion",
            "snapshotSha256",
            "description",
            "compareToSha256",
            "notableChanges",
        },
        "snapshot note",
    )
    if root.get("format") != SNAPSHOT_NOTE_FORMAT:
        raise SnapshotProvenanceError(
            f"snapshot note: 'format' must be {SNAPSHOT_NOTE_FORMAT!r}"
        )
    if root.get("formatVersion") != SNAPSHOT_NOTE_VERSION:
        raise SnapshotProvenanceError(
            f"snapshot note: unsupported formatVersion {root.get('formatVersion')!r}"
        )

    note_sha256 = _sha256(root.get("snapshotSha256"), "snapshot note.snapshotSha256")
    _string(root.get("description"), "snapshot note.description")
    if "compareToSha256" in root:
        compare_sha256 = _sha256(root["compareToSha256"], "snapshot note.compareToSha256")
        if compare_sha256 == note_sha256:
            raise SnapshotProvenanceError(
                "snapshot note.compareToSha256 must identify a different snapshot"
            )

    notable_changes = root.get("notableChanges")
    if not isinstance(notable_changes, list):
        raise SnapshotProvenanceError("snapshot note.notableChanges must be an array")
    for index, change in enumerate(notable_changes):
        _string(change, f"snapshot note.notableChanges[{index}]")

    if snapshot_sha256 is not None:
        expected = _sha256(snapshot_sha256, "snapshot_sha256")
        if note_sha256 != expected:
            raise SnapshotProvenanceError(
                f"Snapshot note references {note_sha256}, expected {expected}"
            )


def load_snapshot_note(
    path: Path,
    *,
    snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Load and validate one human-authored snapshot-note document."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotProvenanceError(f"Could not load snapshot note {path}: {exc}") from exc
    validate_snapshot_note(document, snapshot_sha256=snapshot_sha256)
    return document


def validate_snapshot_note_directory(directory: Path) -> tuple[Path, ...]:
    """Validate every maintained snapshot-note JSON file below ``directory``."""
    if not directory.is_dir():
        raise SnapshotProvenanceError(f"Snapshot-note directory does not exist: {directory}")

    notes: list[Path] = []
    unsupported: list[Path] = []
    for path in sorted(candidate for candidate in directory.rglob("*") if candidate.is_file()):
        relative = path.relative_to(directory)
        if relative == Path("README.md"):
            continue
        if path.suffix.casefold() == ".json":
            notes.append(path)
        else:
            unsupported.append(relative)

    if unsupported:
        rendered = ", ".join(path.as_posix() for path in unsupported)
        raise SnapshotProvenanceError(
            f"Snapshot-note directory contains unsupported file(s): {rendered}"
        )

    for path in notes:
        load_snapshot_note(path)
    return tuple(notes)


def _snapshot_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != name
    ):
        raise SnapshotProvenanceError(
            f"Snapshot ZIP member must be a normalized relative POSIX path: {name!r}"
        )
    return name


def _artifact_record(value: Any, context: str) -> dict[str, Any]:
    record = _object(value, context)
    _only_keys(record, {"name", "path", "sha256"}, context)
    _string(record.get("name"), f"{context}.name")
    _sha256(record.get("sha256"), f"{context}.sha256")
    if "path" in record:
        path = _string(record["path"], f"{context}.path")
        portable = PurePosixPath(path)
        if (
            "\\" in path
            or portable.is_absolute()
            or ".." in portable.parts
            or re.match(r"^[A-Za-z]:/", path)
        ):
            raise SnapshotProvenanceError(
                f"{context}.path must be a portable project-relative POSIX path"
            )
    return record


def _iso_date(value: Any, context: str) -> date:
    text = _string(value, context)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SnapshotProvenanceError(f"{context} must be an ISO date") from exc


def _aware_datetime(value: str, context: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SnapshotProvenanceError(f"{context} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotProvenanceError(f"{context} must include a timezone offset")
    return parsed


def _sha256(value: Any, context: str) -> str:
    text = _string(value, context)
    if not _SHA256_RE.fullmatch(text):
        raise SnapshotProvenanceError(f"{context} must be a lowercase SHA-256 digest")
    return text


def _string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotProvenanceError(f"{context} must be a non-empty string")
    return value


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotProvenanceError(f"{context} must be an object")
    return value


def _only_keys(value: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise SnapshotProvenanceError(
            f"{context} has unknown field(s): {', '.join(sorted(unknown))}"
        )


def _canonical_pretty_json(document: dict[str, Any]) -> str:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
