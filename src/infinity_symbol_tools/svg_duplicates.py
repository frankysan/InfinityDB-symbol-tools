"""Standalone exact/visual SVG duplicate detection with pinned source evidence.

The existing svg_processor algorithm is unchanged. This stage supplies verified
effective SVG bytes and font classifications, preserves the three legacy CSV
reports, and emits a digest-pinned receipt for the canonical identity map.
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
    _checked_path,
    _load_pinned_effective_assets,
    _publish_reports,
)

FONT_AUDIT_FORMAT = "InfinityDB SVG font audit"


class DuplicateResult(NamedTuple):
    groups: Path
    errors: Path
    summary_csv: Path
    receipt: Path
    summary: dict[str, int]
    canonical_by_archive_path: dict[str, str]
    status: str


def _read_verified_font_audit(
    archive_path: Path,
    font_report: Path,
    font_receipt: Path,
    expected_paths: set[str],
) -> dict[str, str]:
    """Require a passed font audit pinned to this exact effective archive."""
    try:
        report = json.loads(font_report.read_text(encoding="utf-8"))
        receipt = json.loads(font_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not load font-audit evidence: {exc}") from exc
    artifact = {"name": archive_path.name, "sha256": sha256_file(archive_path)}
    if (
        not isinstance(report, dict)
        or report.get("format") != FONT_AUDIT_FORMAT
        or report.get("formatVersion") != 1
        or report.get("symbolArtifact") != artifact
        or not isinstance(receipt, dict)
        or receipt.get("format") != STAGE_RECEIPT_FORMAT
        or receipt.get("formatVersion") != STAGE_RECEIPT_VERSION
        or receipt.get("stage") != "fontAudit"
        or receipt.get("status") != "passed"
        or receipt.get("inputArtifact") != artifact
        or receipt.get("summary") != report.get("summary")
        or receipt.get("report") != {
            "name": font_report.name,
            "sha256": sha256_file(font_report),
        }
        or receipt.get("fontAliases") != report.get("fontAliases")
        or receipt.get("fontIndex") != report.get("fontIndex")
    ):
        raise ValueError("Duplicate detection requires passed, pinned font-audit evidence")
    rows = report.get("assets")
    if not isinstance(rows, list):
        raise ValueError("Font-audit asset classifications must be an array")
    categories: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid font-audit asset classification")
        path, category = row.get("archivePath"), row.get("category")
        if (
            not isinstance(path, str)
            or path in categories
            or category not in {"fonts_available", "no_active_text"}
        ):
            raise ValueError("Invalid or repeated font-audit asset classification")
        categories[path] = category
    if set(categories) != expected_paths:
        raise ValueError("Font-audit classifications differ from effective SVG set")
    summary = report.get("summary")
    if not isinstance(summary, dict) or (
        summary.get("svgCount") != len(categories)
        or summary.get("fontMissingAssetCount") != 0
    ):
        raise ValueError("Font-audit classification totals differ from effective SVG set")
    return categories


def detect_effective_symbol_duplicates(
    effective_archive: Path,
    effective_manifest: Path,
    effective_report: Path,
    font_report: Path,
    font_receipt: Path,
    reports_directory: Path,
    receipt_path: Path,
    *,
    render_size: int = 512,
    jobs: int = 4,
    renderer: str = "resvg",
) -> DuplicateResult:
    """Find canonical SVGs without modifying the immutable effective source ZIP.

    A nonzero render-error count results in a *failed* stage receipt, retaining
    exact-duplicate observations and error details for diagnostics.
    """
    if render_size < 1 or jobs < 1:
        raise ValueError("Duplicate render size and jobs must be positive")
    _report, assets = _load_pinned_effective_assets(
        effective_archive, effective_manifest, effective_report,
    )
    expected = {_checked_path(row["archivePath"]): row["sha256"] for row in assets}
    if len(expected) != len(assets):
        raise ValueError("Duplicate effective archive paths in processing source")
    if len({path.casefold() for path in expected}) != len(expected):
        raise ValueError("Case-insensitive effective archive member collision")
    categories = _read_verified_font_audit(
        effective_archive, font_report, font_receipt, set(expected),
    )

    with tempfile.TemporaryDirectory(prefix=".symbol-dedup-") as temporary:
        work = Path(temporary)
        raw_root = work / "effective"
        with zipfile.ZipFile(effective_archive) as archive:
            filenames = [name for name in archive.namelist() if not name.endswith("/")]
            if len(filenames) != len(set(filenames)) or set(filenames) != set(expected):
                raise ValueError("Effective ZIP members differ from approved source identities")
            for path, sha256 in sorted(expected.items()):
                data = archive.read(path)
                if hashlib.sha256(data).hexdigest() != sha256:
                    raise ValueError(f"Effective SVG member SHA-256 mismatch: {path}")
                target = raw_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

        result = svg_processor.find_duplicate_svgs(
            raw_root,
            work / "scratch",
            categories,
            render_size=render_size,
            jobs=jobs,
            renderer=renderer,
            reports_root=work / "reports",
        )
        redundant = result["duplicate_representatives"]
        if not isinstance(redundant, dict) or any(
            not isinstance(source, str) or not isinstance(target, str)
            for source, target in redundant.items()
        ):
            raise ValueError("Duplicate detector returned an invalid representative mapping")
        if any(
            source not in expected or target not in expected
            for source, target in redundant.items()
        ):
            raise ValueError("Duplicate detector chose an unknown source or representative")
        canonical = {path: redundant.get(path, path) for path in sorted(expected)}
        if any(canonical[target] != target for target in canonical.values()):
            raise ValueError("Duplicate detector returned chained canonical representatives")
        summary = {
            "sourceAssetCount": result["source_svg_files"],
            "uniqueByteSetCount": result["unique_byte_sets"],
            "rendersAvoidedExactCount": result["renders_avoided_exact"],
            "exactGroupCount": result["exact_groups"],
            "visualGroupCount": result["visual_groups"],
            "redundantAssetCount": result["redundant_files"],
            "canonicalAssetCount": len(set(canonical.values())),
            "renderErrorCount": result["render_errors"],
            "sourceAssetBytes": result["source_size_bytes"],
            "canonicalAssetBytes": result["canonical_size_bytes"],
            "reclaimedAssetBytes": result["reclaimed_size_bytes"],
        }
        if (
            summary["sourceAssetCount"] != len(expected)
            or summary["canonicalAssetCount"] != result["canonical_svg_files"]
            or summary["redundantAssetCount"] != len(redundant)
        ):
            raise ValueError("Duplicate detector counts differ from the verified source set")
        status = "failed" if summary["renderErrorCount"] else "passed"
        names = {
            "groups": (reports_directory / "duplicate-groups.csv", result["report_path"]),
            "errors": (reports_directory / "duplicate-render-errors.csv", result["error_path"]),
            "summary": (reports_directory / "duplicate-summary.csv", result["summary_path"]),
        }
        published = [(dest, Path(src).read_bytes()) for dest, src in names.values()]
        receipt = {
            "format": STAGE_RECEIPT_FORMAT,
            "formatVersion": STAGE_RECEIPT_VERSION,
            "stage": "duplicateDetection",
            "status": status,
            "inputArtifact": {
                "name": effective_archive.name,
                "sha256": sha256_file(effective_archive),
            },
            "fontAuditReport": {
                "name": font_report.name,
                "sha256": sha256_file(font_report),
            },
            "fontAuditReceipt": {
                "name": font_receipt.name,
                "sha256": sha256_file(font_receipt),
            },
            "renderer": result["renderer"],
            "rendererVersion": result["renderer_version"],
            "renderSize": result["render_size"],
            "jobs": result["jobs"],
            "reports": {
                label: {
                    "name": destination.name,
                    "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
                }
                for label, (destination, source) in names.items()
            },
            "summary": dict(sorted(summary.items())),
            "canonicalByArchivePath": canonical,
        }
        _publish_reports([*published, (receipt_path, _canonical(receipt))])
    return DuplicateResult(
        names["groups"][0], names["errors"][0], names["summary"][0],
        receipt_path, summary, canonical, status,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-archive", type=Path, required=True)
    parser.add_argument("--effective-manifest", type=Path, required=True)
    parser.add_argument("--effective-report", type=Path, required=True)
    parser.add_argument("--font-report", type=Path, required=True)
    parser.add_argument("--font-receipt", type=Path, required=True)
    parser.add_argument("--reports-directory", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--render-size", type=int, default=512)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--renderer", default="resvg")
    args = parser.parse_args(argv)
    try:
        result = detect_effective_symbol_duplicates(
            args.effective_archive, args.effective_manifest, args.effective_report,
            args.font_report, args.font_receipt, args.reports_directory,
            args.receipt, render_size=args.render_size, jobs=args.jobs,
            renderer=args.renderer,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"Duplicate detection {result.status}: "
        f"{result.summary['canonicalAssetCount']} canonical; "
        f"{result.summary['redundantAssetCount']} redundant; "
        f"{result.summary['renderErrorCount']} rendering errors; "
        f"receipt: {result.receipt}"
    )
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
