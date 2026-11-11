"""Resolve installed fonts used by a verified standalone SVG preflight.

The JSON report retains InfinityDB's current font-audit shape. A separate
receipt pins the exact installed font bytes and the prior preflight evidence.
Missing or ambiguous fonts fail this stage without changing the SVG ZIP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any, NamedTuple

from infinity_symbol_tools import svg_processor
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_preflight import (
    STAGE_RECEIPT_FORMAT,
    STAGE_RECEIPT_VERSION,
    _canonical,
    _load_pinned_effective_assets,
    _publish_reports,
)

FONT_AUDIT_FORMAT = "InfinityDB SVG font audit"
FONT_AUDIT_VERSION = 1
SVG_PREFLIGHT_FORMAT = "InfinityDB SVG preflight audit"


class FontAuditResult(NamedTuple):
    report: Path
    receipt: Path
    summary: dict[str, int]
    status: str


def _font_result_record(result: dict[str, str]) -> dict[str, str]:
    font_file = result.get("font_file", "")
    return {
        "status": result.get("status", ""),
        "matchType": result.get("match_type", ""),
        "matchedName": result.get("matched_name", ""),
        "family": result.get("family", ""),
        "subfamily": result.get("subfamily", ""),
        "fullName": result.get("full_name", ""),
        "postscript": result.get("postscript", ""),
        "normalize": result.get("normalize", ""),
        "weight": result.get("weight", ""),
        "style": result.get("style", ""),
        "stretch": result.get("stretch", ""),
        "fontFile": Path(font_file).name if font_file else "",
    }


def _read_preflight(
    archive: Path,
    preflight_report: Path,
    preflight_receipt: Path,
) -> dict[str, Any]:
    try:
        prior = json.loads(preflight_report.read_text(encoding="utf-8"))
        state = json.loads(preflight_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load SVG preflight evidence: {exc}") from exc
    artifact = {"name": archive.name, "sha256": sha256_file(archive)}
    if (
        not isinstance(prior, dict)
        or prior.get("format") != SVG_PREFLIGHT_FORMAT
        or prior.get("formatVersion") != 1
        or prior.get("symbolArtifact") != artifact
        or not isinstance(state, dict)
        or state.get("format") != STAGE_RECEIPT_FORMAT
        or state.get("formatVersion") != STAGE_RECEIPT_VERSION
        or state.get("stage") != "svgPreflight"
        or state.get("status") != "passed"
        or state.get("inputArtifact") != artifact
        or state.get("summary") != prior.get("summary")
        or state.get("report") != {
            "name": preflight_report.name,
            "sha256": sha256_file(preflight_report),
        }
    ):
        raise ValueError("Font audit requires passed, source-pinned SVG preflight evidence")
    if (
        not isinstance(prior.get("assets"), list)
        or not isinstance(prior.get("summary"), dict)
        or prior["summary"].get("parseErrorCount") != 0
        or prior["summary"].get("svgCount") != len(prior["assets"])
    ):
        raise ValueError("SVG preflight asset/summary counts are invalid")
    return prior


def _resolved_font_files(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Hash the actual installed font source, never distribute font binaries."""
    files: dict[str, str] = {}
    for row in rows:
        for font in row["effectiveFonts"]:
            source = font.get("_sourceFile")
            if not source:
                continue
            path = Path(source)
            if not path.is_file():
                raise ValueError(f"Resolved font file is no longer available: {path}")
            digest = sha256_file(path)
            existing = files.get(path.name)
            if existing is not None and existing != digest:
                raise ValueError(
                    "Different resolved font files share a basename: "
                    f"{path.name}"
                )
            files[path.name] = digest
    return [
        {"name": name, "sha256": digest}
        for name, digest in sorted(files.items(), key=lambda pair: pair[0].casefold())
    ]


def audit_effective_symbol_fonts(
    effective_archive: Path,
    effective_manifest: Path,
    effective_report: Path,
    preflight_report: Path,
    preflight_receipt: Path,
    output_report: Path,
    output_receipt: Path,
    *,
    alias_config: Path = svg_processor.DEFAULT_FONT_ALIAS_CONFIG,
) -> FontAuditResult:
    """Audit SVG font references against a frozen effective ZIP and local font index."""
    _result, assets = _load_pinned_effective_assets(
        effective_archive, effective_manifest, effective_report,
    )
    prior = _read_preflight(effective_archive, preflight_report, preflight_receipt)
    expected_names = {row["archivePath"] for row in assets}
    if {row.get("archivePath") for row in prior["assets"]} != expected_names:
        raise ValueError("Passed preflight assets differ from effective input")

    overrides = svg_processor.load_font_reference_overrides(alias_config)
    exact_index, compact_index, font_file_count, face_count = svg_processor.load_font_index()
    rows: list[dict[str, Any]] = []
    references: dict[str, dict[str, Any]] = {}
    available_assets = 0
    missing_assets = 0
    no_text_assets = 0
    implicit_default_assets = 0
    unused_declarations = 0

    with tempfile.TemporaryDirectory(prefix=".symbol-font-audit-") as staging:
        temp = Path(staging)
        with zipfile.ZipFile(effective_archive) as archive:
            for asset in assets:
                archive_path = asset["archivePath"]
                body = archive.read(archive_path)
                if hashlib.sha256(body).hexdigest() != asset["sha256"]:
                    raise ValueError(f"Effective font audit input changed: {archive_path}")
                file = temp / archive_path
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(body)
                scan, error = svg_processor.scan_svg(file)
                if scan is None:
                    raise ValueError(
                        f"Font audit could not parse {archive_path} after passed preflight: {error}"
                    )
                used_fonts = scan["used_fonts"]
                declared_fonts = scan["declared_fonts"]
                active_text = scan["found_active_text"]
                used_keys = {svg_processor.normal_key(value) for value in used_fonts}
                unused = sorted(
                    (
                        value for value in declared_fonts
                        if svg_processor.normal_key(value) not in used_keys
                    ),
                    key=str.casefold,
                )
                unused_declarations += len(unused)
                resolved: list[dict[str, Any]] = []
                has_unresolved = False
                for reference in sorted(used_fonts, key=str.casefold):
                    match = svg_processor.find_font(
                        reference, exact_index, compact_index, overrides=overrides,
                    )
                    if match["status"] in {"MISSING", "AMBIGUOUS"}:
                        has_unresolved = True
                    record = _font_result_record(match)
                    resolved.append({
                        "reference": reference, "textRuns": used_fonts[reference],
                        **record, "_sourceFile": match.get("font_file", ""),
                    })
                    aggregate = references.setdefault(
                        reference, {
                            "reference": reference, "assetCount": 0, "textRuns": 0,
                            **record,
                        },
                    )
                    comparable = {
                        key: value for key, value in aggregate.items()
                        if key not in {"reference", "assetCount", "textRuns"}
                    }
                    if comparable != record:
                        raise ValueError(
                            f"Font reference {reference!r} resolved inconsistently"
                        )
                    aggregate["assetCount"] += 1
                    aggregate["textRuns"] += used_fonts[reference]
                if not active_text:
                    category = "no_active_text"
                    no_text_assets += 1
                elif not used_fonts:
                    category = "fonts_available"
                    available_assets += 1
                    implicit_default_assets += 1
                elif has_unresolved:
                    category = "fonts_missing"
                    missing_assets += 1
                else:
                    category = "fonts_available"
                    available_assets += 1
                rows.append({
                    "archivePath": archive_path,
                    "category": category,
                    "activeText": active_text,
                    "textRunCount": scan["text_runs"],
                    "emptyTextObjectCount": scan["empty_text_objects"],
                    "effectiveFonts": resolved,
                    "unusedFontDeclarations": unused,
                })

    # The report preserves the old field contract; the receipt separately
    # pins the exact installed-font bytes, without disclosing local full paths.
    font_sources = _resolved_font_files(rows)
    for row in rows:
        for record in row["effectiveFonts"]:
            record.pop("_sourceFile", None)
    reference_rows = sorted(references.values(), key=lambda row: row["reference"].casefold())
    summary = {
        "svgCount": len(rows),
        "fontAvailableAssetCount": available_assets,
        "fontMissingAssetCount": missing_assets,
        "noActiveTextAssetCount": no_text_assets,
        "implicitDefaultAssetCount": implicit_default_assets,
        "effectiveFontReferenceCount": len(reference_rows),
        "availableFontReferenceCount": sum(
            row["status"] == "FOUND" for row in reference_rows
        ),
        "missingFontReferenceCount": sum(
            row["status"] == "MISSING" for row in reference_rows
        ),
        "ambiguousFontReferenceCount": sum(
            row["status"] == "AMBIGUOUS" for row in reference_rows
        ),
        "genericFontReferenceCount": sum(
            row["status"] == "GENERIC" for row in reference_rows
        ),
        "normalizedAliasReferenceCount": sum(
            row["status"] == "FOUND" and row["normalize"] == "YES"
            for row in reference_rows
        ),
        "unusedDeclarationCount": unused_declarations,
    }
    if summary["svgCount"] != len(assets):
        raise ValueError("Font audit count differs from effective source asset count")
    report = {
        "format": FONT_AUDIT_FORMAT,
        "formatVersion": FONT_AUDIT_VERSION,
        "symbolArtifact": {
            "name": effective_archive.name,
            "sha256": sha256_file(effective_archive),
        },
        "fontAliases": {
            "name": alias_config.name,
            "sha256": sha256_file(alias_config),
        },
        "fontIndex": {
            "fontFileCount": font_file_count,
            "fontFaceCount": face_count,
        },
        "summary": dict(sorted(summary.items())),
        "fonts": reference_rows,
        "assets": sorted(rows, key=lambda row: row["archivePath"]),
    }
    payload = _canonical(report)
    status = "passed" if missing_assets == 0 else "failed"
    receipt = {
        "format": STAGE_RECEIPT_FORMAT,
        "formatVersion": STAGE_RECEIPT_VERSION,
        "stage": "fontAudit",
        "status": status,
        "inputArtifact": report["symbolArtifact"],
        "preflightReport": {
            "name": preflight_report.name,
            "sha256": sha256_file(preflight_report),
        },
        "preflightReceipt": {
            "name": preflight_receipt.name,
            "sha256": sha256_file(preflight_receipt),
        },
        "fontAliases": report["fontAliases"],
        "fontIndex": report["fontIndex"],
        "resolvedFontFiles": font_sources,
        "report": {
            "name": output_report.name,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "summary": dict(sorted(summary.items())),
    }
    _publish_reports(
        [(output_report, payload), (output_receipt, _canonical(receipt))],
    )
    return FontAuditResult(output_report, output_receipt, summary, status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-archive", type=Path, required=True)
    parser.add_argument("--effective-manifest", type=Path, required=True)
    parser.add_argument("--effective-report", type=Path, required=True)
    parser.add_argument("--preflight-report", type=Path, required=True)
    parser.add_argument("--preflight-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--font-aliases", type=Path,
        default=svg_processor.DEFAULT_FONT_ALIAS_CONFIG,
    )
    args = parser.parse_args(argv)
    try:
        result = audit_effective_symbol_fonts(
            args.effective_archive, args.effective_manifest, args.effective_report,
            args.preflight_report, args.preflight_receipt, args.output, args.receipt,
            alias_config=args.font_aliases,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"SVG font audit {result.status}: "
        f"{result.summary['fontAvailableAssetCount']} fonts available, "
        f"{result.summary['fontMissingAssetCount']} missing; "
        f"report: {result.report}; receipt: {result.receipt}"
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
