"""Deterministic SVG preflight for immutable effective-symbol snapshots.

The report fields and counters intentionally match InfinityDB's maintained
SVG preflight audit. This independent stage does not mutate the override
artifact or claim font resolution/conversion/compression is complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from infinity_symbol_tools.snapshot_provenance import (
    load_snapshot_manifest,
    sha256_file,
)

SVG_PREFLIGHT_FORMAT = "InfinityDB SVG preflight audit"
SVG_PREFLIGHT_VERSION = 1
STAGE_RECEIPT_FORMAT = "InfinityDB symbol processing stage receipt"
STAGE_RECEIPT_VERSION = 1
_EFFECTIVE_FORMAT = "InfinityDB effective symbol processing report"
_TEXT_ROOT_TAGS = {"text", "flowRoot"}
_FONT_FAMILY_RE = re.compile(r"(?:^|[;{])\s*font-family\s*:\s*([^;}]+)", re.I)


class PreflightResult(NamedTuple):
    report: Path
    receipt: Path
    summary: dict[str, int]
    status: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _split_font_family_list(value: str) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in {"'", '"'}:
            quote = char
        elif char == ",":
            name = "".join(current).strip()
            if name:
                result.append(name)
            current = []
        else:
            current.append(char)
    name = "".join(current).strip()
    if name:
        result.append(name)
    return result


def _font_families(root: ET.Element) -> list[str]:
    families: set[str] = set()
    for element in root.iter():
        direct = element.attrib.get("font-family")
        if direct:
            families.update(_split_font_family_list(direct))
        style = element.attrib.get("style")
        if style:
            for match in _FONT_FAMILY_RE.finditer(";" + style):
                families.update(_split_font_family_list(match.group(1)))
        if _local_name(element.tag) == "style":
            css = "".join(element.itertext())
            for match in _FONT_FAMILY_RE.finditer("{" + css):
                families.update(_split_font_family_list(match.group(1)))
    return sorted(families, key=str.casefold)


def _text_state(root: ET.Element) -> tuple[bool, int, int]:
    active = False
    text_runs = 0
    empty_objects = 0
    for element in root.iter():
        if _local_name(element.tag) not in _TEXT_ROOT_TAGS:
            continue
        text = "".join(element.itertext())
        if text.strip():
            active = True
            text_runs += sum(1 for part in element.itertext() if part.strip())
        else:
            empty_objects += 1
    return active, text_runs, empty_objects


def _load_pinned_effective_assets(
    archive_path: Path, snapshot_manifest: Path, effective_report: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Verify effective snapshot identity and per-member byte hashes before parsing."""
    provenance = load_snapshot_manifest(snapshot_manifest, archive=archive_path)
    report = json.loads(effective_report.read_text(encoding="utf-8"))
    if (
        not isinstance(report, dict)
        or report.get("format") != _EFFECTIVE_FORMAT
        or report.get("formatVersion") != 1
    ):
        raise ValueError("Unrecognized effective-symbol input report")
    stages = report.get("processingStages")
    if stages != {
        "overrides": "completed",
        "fontAudit": "pending",
        "deduplication": "pending",
        "textConversion": "pending",
        "compression": "pending",
    }:
        raise ValueError("SVG preflight requires completed overrides and pending later stages")
    snapshot = report.get("snapshot")
    if not isinstance(snapshot, dict) or (
        snapshot.get("archiveName") != archive_path.name
        or snapshot.get("sha256") != sha256_file(archive_path)
        or snapshot.get("manifestName") != snapshot_manifest.name
        or snapshot.get("manifestSha256") != sha256_file(snapshot_manifest)
        or snapshot.get("documentCount") != provenance["snapshot"]["documentCount"]
    ):
        raise ValueError("SVG preflight effective archive/provenance identity mismatch")
    if provenance["snapshot"]["type"] != "symbols":
        raise ValueError("SVG preflight input must be an immutable symbol archive")
    assets = report.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("SVG preflight requires nonempty effective assets")
    return report, assets


def _checked_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Unsafe SVG archive member path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.suffix.casefold() != ".svg"
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"Unsafe SVG archive member path: {value!r}")
    return value


def _scan_svg_bytes(path: str, body: bytes) -> dict[str, Any]:
    """Match the existing structural preflight's parse/text/font semantics."""
    row: dict[str, Any] = {"archivePath": path}
    try:
        root = ET.fromstring(body)
        if _local_name(root.tag) != "svg":
            raise ValueError(f"root element is {_local_name(root.tag)!r}, expected 'svg'")
        active_text, text_runs, empty_text_objects = _text_state(root)
        fonts = _font_families(root)
        row.update({
            "parseStatus": "ok",
            "activeText": active_text,
            "textRunCount": text_runs,
            "emptyTextObjectCount": empty_text_objects,
            "fontFamilies": fonts,
        })
    except (ET.ParseError, ValueError) as exc:
        row.update({"parseStatus": "error", "error": str(exc)})
    return row


def scan_effective_symbol_preflight(
    archive_path: Path,
    snapshot_manifest: Path,
    effective_report: Path,
    report_path: Path,
    receipt_path: Path,
) -> PreflightResult:
    """Validate all source bytes and write the matching SVG preflight report.

    A parse failure is *reported* and marked failed. An artifact mismatch
    instead fails closed before creating any report.
    """
    _report, assets = _load_pinned_effective_assets(
        archive_path, snapshot_manifest, effective_report,
    )
    paths: set[str] = set()
    rows: list[dict[str, Any]] = []
    families: Counter[str] = Counter()
    parse_errors = 0
    active_text_assets = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(members) != len(set(members)):
            raise ValueError("SVG preflight archive has duplicate ZIP members")
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError("SVG preflight has an invalid asset row")
            path = _checked_path(asset.get("archivePath"))
            folded = path.casefold()
            if folded in paths:
                raise ValueError(f"SVG preflight has duplicate/colliding archive path: {path}")
            paths.add(folded)
            try:
                body = archive.read(path)
            except KeyError as exc:
                raise ValueError(f"SVG preflight missing effective member: {path}") from exc
            if hashlib.sha256(body).hexdigest() != asset.get("sha256"):
                raise ValueError(f"SVG preflight effective member SHA-256 mismatch: {path}")
            row = _scan_svg_bytes(path, body)
            if row["parseStatus"] == "error":
                parse_errors += 1
            else:
                if row["activeText"]:
                    active_text_assets += 1
                for family in row["fontFamilies"]:
                    families[family] += 1
            rows.append(row)
    if set(members) != {row["archivePath"] for row in rows}:
        raise ValueError("SVG preflight archive members differ from effective report")

    summary = {
        "svgCount": len(rows),
        "parseErrorCount": parse_errors,
        "activeTextAssetCount": active_text_assets,
        "noActiveTextAssetCount": len(rows) - active_text_assets - parse_errors,
        "fontDeclaredAssetCount": sum(1 for row in rows if row.get("fontFamilies")),
        "uniqueDeclaredFontCount": len(families),
    }
    artifact = {"name": archive_path.name, "sha256": sha256_file(archive_path)}
    document = {
        "format": SVG_PREFLIGHT_FORMAT,
        "formatVersion": SVG_PREFLIGHT_VERSION,
        "symbolArtifact": artifact,
        "summary": dict(sorted(summary.items())),
        "fonts": [
            {"family": family, "assetCount": count}
            for family, count in sorted(families.items(), key=lambda pair: pair[0].casefold())
        ],
        "assets": sorted(rows, key=lambda row: row["archivePath"]),
    }
    report_payload = _canonical(document)
    status = "passed" if parse_errors == 0 else "failed"
    receipt = {
        "format": STAGE_RECEIPT_FORMAT,
        "formatVersion": STAGE_RECEIPT_VERSION,
        "stage": "svgPreflight",
        "status": status,
        "inputArtifact": artifact,
        "report": {
            "name": report_path.name,
            "sha256": hashlib.sha256(report_payload).hexdigest(),
        },
        "summary": dict(sorted(summary.items())),
    }
    _publish_reports(
        [(report_path, report_payload), (receipt_path, _canonical(receipt))],
    )
    return PreflightResult(report_path, receipt_path, summary, status)


def _canonical(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _publish_reports(items: list[tuple[Path, bytes]]) -> None:
    if len({path.resolve() for path, _data in items}) != len(items):
        raise ValueError("Output reports must have different paths")
    if any(path.exists() for path, _data in items):
        raise FileExistsError("SVG preflight does not overwrite an existing report")
    published: list[Path] = []
    try:
        for path, data in items:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                published.append(path)
                stream.write(data)
    except BaseException:
        for path in reversed(published):
            path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-archive", type=Path, required=True)
    parser.add_argument("--effective-manifest", type=Path, required=True)
    parser.add_argument("--effective-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = scan_effective_symbol_preflight(
            args.effective_archive, args.effective_manifest, args.effective_report,
            args.output, args.receipt,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"SVG preflight {result.status}: {result.summary['svgCount']} assets; "
        f"{result.summary['parseErrorCount']} parse errors; "
        f"report: {result.report}; receipt: {result.receipt}"
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
