"""Validate portable source requests before raw-symbol acquisition.

This module is deliberately read-only and has no InfinityDB runtime imports.
It does not decide which URLs are authoritative or download any asset.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

FORMAT = "InfinityDB raw symbol acquisition requests"
VERSION = 1
_HOST = "assets.corvusbelli.net"
_ROOT = "/army/img/"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PORTABLE_PATH_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*\.svg\Z")


class SourcePlanError(ValueError):
    """Invalid or unpinned raw-symbol acquisition request contract."""


def _object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise SourcePlanError(f"{label} requires exactly {sorted(fields)}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source_plan(
    document: Any, *, army_archive: Path | None = None,
) -> dict[str, Any]:
    """Check exact schema, URL safety, path uniqueness and optional Army pin."""
    plan = _object(
        document,
        {"format", "formatVersion", "inputArtifact", "sourceDocumentCount", "requests"},
        "source plan",
    )
    if plan["format"] != FORMAT or type(plan["formatVersion"]) is not int:
        raise SourcePlanError("Unknown raw-symbol source plan format/version")
    if plan["formatVersion"] != VERSION:
        raise SourcePlanError("Unsupported raw-symbol source plan version")

    army = _object(plan["inputArtifact"], {"name", "sha256"}, "inputArtifact")
    name, digest = army["name"], army["sha256"]
    if (
        not isinstance(name, str)
        or not name.lower().endswith(".zip")
        or "/" in name
        or "\\" in name
        or name in {"", ".", ".."}
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
    ):
        raise SourcePlanError("inputArtifact must pin a ZIP basename and lowercase SHA-256")

    count = plan["sourceDocumentCount"]
    if type(count) is not int or count < 2:
        raise SourcePlanError("sourceDocumentCount must include metadata and at least one Army")
    requests = plan["requests"]
    if not isinstance(requests, list) or not requests:
        raise SourcePlanError("requests must be a nonempty array")
    urls: list[str] = []
    paths: set[str] = set()
    for index, record in enumerate(requests):
        row = _object(record, {"url", "archivePath"}, f"requests[{index}]")
        url, archive_path = row["url"], row["archivePath"]
        if not isinstance(url, str):
            raise SourcePlanError(f"requests[{index}].url must be a string")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != _HOST
            or not parsed.path.startswith(_ROOT)
        ):
            raise SourcePlanError(f"requests[{index}] has an unsupported symbol URL: {url}")
        if (
            not isinstance(archive_path, str)
            or "\\" in archive_path
            or _PORTABLE_PATH_RE.fullmatch(archive_path) is None
            or any(part in {"", ".", ".."} for part in archive_path.split("/"))
            or PurePosixPath(archive_path).is_absolute()
        ):
            raise SourcePlanError(f"requests[{index}] has an unsafe archive path")
        folded = archive_path.casefold()
        if folded in paths:
            raise SourcePlanError(f"Duplicate case-insensitive archive path: {archive_path}")
        urls.append(url)
        paths.add(folded)
    if urls != sorted(set(urls)):
        raise SourcePlanError("requests must be sorted by unique URL")

    if army_archive is not None:
        if army_archive.name != name:
            raise SourcePlanError("Army ZIP basename does not match inputArtifact")
        if not army_archive.is_file():
            raise SourcePlanError(f"Army ZIP does not exist: {army_archive}")
        if sha256_file(army_archive) != digest:
            raise SourcePlanError("Army ZIP SHA-256 does not match inputArtifact")

    return plan


def load_source_plan(
    path: Path, *, army_archive: Path | None = None,
) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourcePlanError(f"Could not load source request plan {path}: {exc}") from exc
    return validate_source_plan(document, army_archive=army_archive)
