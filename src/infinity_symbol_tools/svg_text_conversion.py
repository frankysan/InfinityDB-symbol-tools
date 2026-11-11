"""Convert verified canonical SVG representatives to paths without publishing symbols.

An immutable, intermediate canonical ZIP is produced only on complete success.
The source/effective archive remains untouched; compression and publication follow.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from infinity_symbol_tools import svg_processor
from infinity_symbol_tools.snapshot_archive import _archive_info
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_duplicates import _read_verified_font_audit
from infinity_symbol_tools.svg_font_audit import _font_result_record
from infinity_symbol_tools.svg_preflight import (
    STAGE_RECEIPT_FORMAT,
    STAGE_RECEIPT_VERSION,
    _canonical,
    _checked_path,
    _load_pinned_effective_assets,
    _publish_reports,
    _scan_svg_bytes,
)


class TextConversionResult(NamedTuple):
    status: str
    receipt: Path
    output_archive: Path | None
    report: Path
    summary_report: Path
    summary: dict[str, int]


def _pin(path: Path) -> dict[str, str]:
    return {"name": path.name, "sha256": sha256_file(path)}


def _verified_canonical_map(
    archive: Path,
    receipt_path: Path,
    groups: Path,
    errors: Path,
    summary_csv: Path,
    approved: dict[str, str],
    categories: dict[str, str],
) -> dict[str, str]:
    all_paths = set(approved)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read duplicate-stage receipt: {exc}") from exc
    if not isinstance(receipt, dict) or any((
        receipt.get("format") != STAGE_RECEIPT_FORMAT,
        receipt.get("formatVersion") != STAGE_RECEIPT_VERSION,
        receipt.get("stage") != "duplicateDetection",
        receipt.get("status") != "passed",
        receipt.get("inputArtifact") != _pin(archive),
        receipt.get("reports") != {
            "groups": _pin(groups),
            "errors": _pin(errors),
            "summary": _pin(summary_csv),
        },
    )):
        raise ValueError("Text conversion requires a passed, source-pinned duplicate stage")
    canonical = receipt.get("canonicalByArchivePath")
    totals = receipt.get("summary")
    if (
        not isinstance(canonical, dict)
        or not isinstance(totals, dict)
        or set(canonical) != all_paths
        or any(not isinstance(value, str) or value not in all_paths for value in canonical.values())
        or any(canonical.get(value) != value for value in canonical.values())
        or any(_checked_path(path) != path for path in canonical)
        or totals.get("sourceAssetCount") != len(all_paths)
        or totals.get("renderErrorCount") != 0
        or totals.get("canonicalAssetCount") != len(set(canonical.values()))
        or totals.get("redundantAssetCount") != len(all_paths) - len(set(canonical.values()))
        or not isinstance(receipt.get("rendererVersion"), str)
        or not receipt.get("rendererVersion")
    ):
        raise ValueError("Duplicate-stage canonical mapping or counts are invalid")

    # A receipt must not relabel a source as a different canonical member while
    # retaining the original pinned duplicate-group CSV.
    seen: set[str] = set()
    representatives: dict[str, set[str]] = {}
    with groups.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            path = row["file"]
            representative = row["representative"]
            if (
                path not in approved or path in seen
                or representative not in approved
                or canonical[path] != representative
                or row["role"] != (
                    "representative" if path == representative else "duplicate"
                )
                or row["classification"] != categories[path]
                or row["byte_sha256"] != approved[path]
                or row["type"] not in {"exact", "visual"}
            ):
                raise ValueError("Duplicate CSV disagrees with verified source identities")
            seen.add(path)
            representatives.setdefault(row["group"], set()).add(representative)
    if (
        any(len(values) != 1 for values in representatives.values())
        or {path for path in all_paths if canonical[path] != path}
        != {path for path in seen if canonical[path] != path}
    ):
        raise ValueError("Duplicate-group rows do not agree with canonical mapping")
    with errors.open("r", encoding="utf-8-sig", newline="") as stream:
        if list(csv.DictReader(stream)):
            raise ValueError("Passed duplicate stage includes unresolved rendering errors")
    with summary_csv.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if (
        len(rows) != 1
        or rows[0]["source_svg_files"] != str(len(all_paths))
        or rows[0]["canonical_svg_files"] != str(len(set(canonical.values())))
        or rows[0]["redundant_files"] != str(len(all_paths) - len(set(canonical.values())))
        or rows[0]["render_errors"] != "0"
        or rows[0]["renderer_version"] != receipt["rendererVersion"]
    ):
        raise ValueError("Duplicate summary CSV disagrees with the passed receipt")
    return canonical


def _verify_conversion_fonts(
    font_report: Path,
    font_receipt: Path,
    aliases: Path,
    canonical_text: set[str],
) -> tuple[Any, Any]:
    report = json.loads(font_report.read_text(encoding="utf-8"))
    state = json.loads(font_receipt.read_text(encoding="utf-8"))
    if (
        not isinstance(report, dict)
        or not isinstance(state, dict)
        or report.get("fontAliases") != _pin(aliases)
        or state.get("fontAliases") != _pin(aliases)
    ):
        raise ValueError("Font aliases have changed since the pinned font audit")
    if not canonical_text:
        return {}, {}
    exact, compact, file_count, face_count = svg_processor.load_font_index()
    if report.get("fontIndex") != {
        "fontFileCount": file_count, "fontFaceCount": face_count,
    }:
        raise ValueError("Installed font index changed since font audit")
    overrides = svg_processor.load_font_reference_overrides(aliases)
    selected = {
        row["archivePath"]: row for row in report["assets"]
        if row.get("category") == "fonts_available" and row.get("activeText")
    }
    if not canonical_text.issubset(selected):
        raise ValueError("Missing canonical font-audit classifications")
    font_files: dict[str, str] = {}
    for path in sorted(selected):
        row = selected[path]
        for record in row["effectiveFonts"]:
            result = svg_processor.find_font(
                record["reference"], exact, compact, overrides=overrides,
            )
            expected = dict(record)
            expected.pop("reference")
            expected.pop("textRuns")
            if _font_result_record(result) != expected:
                raise ValueError(f"Font resolution changed for {path}: {record['reference']}")
            font_path = result.get("font_file")
            if font_path:
                source = Path(font_path)
                digest = sha256_file(source)
                if source.name in font_files and font_files[source.name] != digest:
                    raise ValueError(f"Installed font filename collision: {source.name}")
                font_files[source.name] = digest
    if state.get("resolvedFontFiles") != [
        {"name": name, "sha256": digest}
        for name, digest in sorted(font_files.items(), key=lambda item: item[0].casefold())
    ]:
        raise ValueError("Installed font bytes differ from the passed font audit")
    return exact, compact


def convert_canonical_symbols(
    effective_archive: Path,
    effective_manifest: Path,
    effective_report: Path,
    font_report: Path,
    font_receipt: Path,
    duplicate_receipt: Path,
    duplicate_groups: Path,
    duplicate_errors: Path,
    duplicate_summary: Path,
    output_archive: Path,
    conversion_report: Path,
    conversion_summary: Path,
    output_receipt: Path,
    *,
    aliases: Path = svg_processor.DEFAULT_FONT_ALIAS_CONFIG,
    text_converter: str = "inkscape-shell",
    jobs: int = 2,
) -> TextConversionResult:
    """Produce canonical-only intermediate output with complete receipt chaining."""
    if jobs < 1:
        raise ValueError("Conversion jobs must be positive")
    destinations = (output_archive, conversion_report, conversion_summary, output_receipt)
    if len({path.resolve() for path in destinations}) != len(destinations):
        raise ValueError("Conversion outputs must be distinct")
    if any(path.exists() for path in destinations):
        raise FileExistsError("Text conversion does not overwrite existing outputs")

    _effective, assets = _load_pinned_effective_assets(
        effective_archive, effective_manifest, effective_report,
    )
    approved = {_checked_path(row["archivePath"]): row["sha256"] for row in assets}
    if len(approved) != len(assets) or len({path.casefold() for path in approved}) != len(approved):
        raise ValueError("Effective source contains repeated or colliding paths")
    categories = _read_verified_font_audit(
        effective_archive, font_report, font_receipt, set(approved),
    )
    canonical = _verified_canonical_map(
        effective_archive, duplicate_receipt, duplicate_groups,
        duplicate_errors, duplicate_summary, approved, categories,
    )
    duplicate_state = json.loads(duplicate_receipt.read_text(encoding="utf-8"))
    if duplicate_state.get("fontAuditReport") != _pin(font_report) or (
        duplicate_state.get("fontAuditReceipt") != _pin(font_receipt)
    ):
        raise ValueError("Duplicate stage is bound to different font-audit evidence")

    representatives = sorted(set(canonical.values()))
    canonical_text = {path for path in representatives if categories[path] == "fonts_available"}
    exact, compact = _verify_conversion_fonts(
        font_report, font_receipt, aliases, canonical_text,
    )
    with tempfile.TemporaryDirectory(prefix=".symbol-text-conversion-") as directory:
        work = Path(directory)
        with zipfile.ZipFile(effective_archive) as source:
            members = [i.filename for i in source.infolist() if not i.is_dir()]
            if len(members) != len(set(members)) or set(members) != set(approved):
                raise ValueError("Effective ZIP members do not match verified identities")
            for path in representatives:
                data = source.read(path)
                if hashlib.sha256(data).hexdigest() != approved[path]:
                    raise ValueError(f"Effective source SHA-256 mismatch: {path}")
                if path in canonical_text:
                    target = work / "fonts_available" / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
        (work / "fonts_available").mkdir(exist_ok=True)
        result = svg_processor.convert_available_svgs(
            work, exact, compact, jobs=jobs, keep_failed=False,
            text_converter=text_converter,
        )
        status = "passed" if result["failed"] == 0 and (
            result["converted"] == len(canonical_text)
            and result["skipped"] == 0 and result["skipped_duplicates"] == 0
            and (not canonical_text or result["converter_version"])
        ) else "failed"
        output_assets: list[dict[str, Any]] = []
        if status == "passed":
            with zipfile.ZipFile(effective_archive) as source:
                for path in representatives:
                    if path in canonical_text:
                        target = work / "text_as_paths" / path
                        if not target.is_file():
                            status = "failed"
                            break
                        body = target.read_bytes()
                    else:
                        body = source.read(path)
                    checked = _scan_svg_bytes(path, body)
                    if checked["parseStatus"] != "ok" or checked["activeText"]:
                        status = "failed"
                        break
                    output_assets.append({
                        "archivePath": path,
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "sourceSha256": approved[path],
                        "converted": path in canonical_text,
                    })
        if status == "passed":
            try:
                output_archive.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output_archive, "x") as destination:
                    with zipfile.ZipFile(effective_archive) as source:
                        for row in output_assets:
                            path = row["archivePath"]
                            body = (
                                (work / "text_as_paths" / path).read_bytes()
                                if row["converted"] else source.read(path)
                            )
                            destination.writestr(_archive_info(path), body)
            except BaseException:
                output_archive.unlink(missing_ok=True)
                raise

        summary = {
            "sourceAssetCount": len(approved),
            "canonicalAssetCount": len(representatives),
            "redundantAssetCount": len(approved) - len(representatives),
            "textCandidateCount": len(canonical_text),
            "convertedCount": result["converted"],
            "conversionFailureCount": result["failed"],
            "outputAssetCount": len(output_assets) if status == "passed" else 0,
        }
        report_bytes = result["report_path"].read_bytes()
        summary_bytes = result["summary_path"].read_bytes()
        receipt = {
            "format": STAGE_RECEIPT_FORMAT,
            "formatVersion": STAGE_RECEIPT_VERSION,
            "stage": "textConversion",
            "status": status,
            "inputArtifact": _pin(effective_archive),
            "fontAuditReport": _pin(font_report),
            "fontAuditReceipt": _pin(font_receipt),
            "duplicateReceipt": _pin(duplicate_receipt),
            "duplicateReports": {
                "groups": _pin(duplicate_groups),
                "errors": _pin(duplicate_errors),
                "summary": _pin(duplicate_summary),
            },
            "fontAliases": _pin(aliases),
            "converter": result["converter"],
            "converterVersion": result["converter_version"],
            "jobs": jobs,
            "reports": {
                "conversion": {
                    "name": conversion_report.name,
                    "sha256": hashlib.sha256(report_bytes).hexdigest(),
                },
                "summary": {
                    "name": conversion_summary.name,
                    "sha256": hashlib.sha256(summary_bytes).hexdigest(),
                },
            },
            "outputArtifact": _pin(output_archive) if status == "passed" else None,
            "outputAssets": output_assets if status == "passed" else [],
            "canonicalByArchivePath": canonical,
            "summary": summary,
        }
        try:
            _publish_reports([
                (conversion_report, report_bytes),
                (conversion_summary, summary_bytes),
                (output_receipt, _canonical(receipt)),
            ])
        except BaseException:
            if status == "passed":
                output_archive.unlink(missing_ok=True)
            raise
    return TextConversionResult(
        status, output_receipt, output_archive if status == "passed" else None,
        conversion_report, conversion_summary, summary,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for field in (
        "effective-archive", "effective-manifest", "effective-report",
        "font-report", "font-receipt", "duplicate-receipt",
        "duplicate-groups", "duplicate-errors", "duplicate-summary",
        "output-archive", "conversion-report", "conversion-summary", "receipt",
    ):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--font-aliases", type=Path, default=svg_processor.DEFAULT_FONT_ALIAS_CONFIG)
    parser.add_argument("--converter", default="inkscape-shell")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        result = convert_canonical_symbols(
            args.effective_archive, args.effective_manifest, args.effective_report,
            args.font_report, args.font_receipt,
            args.duplicate_receipt, args.duplicate_groups,
            args.duplicate_errors, args.duplicate_summary,
            args.output_archive, args.conversion_report, args.conversion_summary,
            args.receipt, aliases=args.font_aliases,
            text_converter=args.converter, jobs=args.jobs,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"Text conversion {result.status}: "
        f"{result.summary['convertedCount']}/{result.summary['textCandidateCount']} converted; "
        f"receipt: {result.receipt}"
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
