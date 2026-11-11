"""Compress a verified canonical SVG ZIP into an immutable intermediate artifact.

This stage does not publish symbols. It chains the text-conversion receipt,
uses the existing raster-validated compressor, and fails closed on missing
members, unverified reports, or unavailable renderer/version evidence.
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

from infinity_symbol_tools import svg_compress
from infinity_symbol_tools.snapshot_archive import _archive_info
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_preflight import (
    STAGE_RECEIPT_FORMAT, STAGE_RECEIPT_VERSION,
    _canonical, _checked_path, _publish_reports, _scan_svg_bytes,
)


class CanonicalCompressionResult(NamedTuple):
    status: str
    output_archive: Path
    receipt: Path
    report: Path
    candidates_report: Path
    run_report: Path
    summary: dict[str, int | float]


def _pin(path: Path) -> dict[str, str]:
    return {"name": path.name, "sha256": sha256_file(path)}


def _verify_input(archive: Path, conversion_receipt: Path) -> dict[str, str]:
    receipt = json.loads(conversion_receipt.read_text(encoding="utf-8"))
    if (
        not isinstance(receipt, dict)
        or receipt.get("format") != STAGE_RECEIPT_FORMAT
        or receipt.get("formatVersion") != STAGE_RECEIPT_VERSION
        or receipt.get("stage") != "textConversion"
        or receipt.get("status") != "passed"
        or receipt.get("outputArtifact") != _pin(archive)
    ):
        raise ValueError("Requires a passed text-conversion receipt pinned to the canonical ZIP")
    assets = receipt.get("outputAssets")
    mapping = receipt.get("canonicalByArchivePath")
    if not isinstance(assets, list) or not isinstance(mapping, dict) or not assets:
        raise ValueError("Text conversion receipt has no verified canonical identities")
    expected: dict[str, str] = {}
    for row in assets:
        if not isinstance(row, dict):
            raise ValueError("Malformed canonical asset")
        path = _checked_path(row.get("archivePath"))
        if path in expected or path.casefold() in {p.casefold() for p in expected}:
            raise ValueError("Duplicate canonical asset identity")
        digest = row.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Missing canonical asset digest")
        expected[path] = digest
    if (
        set(mapping.values()) != set(expected)
        or any(not isinstance(path, str) or not isinstance(value, str)
               or mapping.get(value) != value for path, value in mapping.items())
    ):
        raise ValueError("Canonical mapping does not match converted assets")
    with zipfile.ZipFile(archive) as source:
        members = [i for i in source.infolist() if not i.is_dir()]
        if (
            len(members) != len(expected)
            or {i.filename for i in members} != set(expected)
            or len({i.filename.casefold() for i in members}) != len(members)
        ):
            raise ValueError("Canonical ZIP members disagree with receipt")
        for member in members:
            if member.file_size > 32 * 1024 * 1024:
                raise ValueError("Canonical SVG exceeds extraction size limit")
            body = source.read(member)
            if hashlib.sha256(body).hexdigest() != expected[member.filename]:
                raise ValueError("Canonical member hash mismatch")
            scan = _scan_svg_bytes(member.filename, body)
            if scan["parseStatus"] != "ok" or scan["activeText"]:
                raise ValueError("Canonical input contains invalid SVG or active text")
    return expected


def _read_compression_rows(report: Path, expected: dict[str, str], profile: str) -> None:
    with report.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if (
        len(rows) != len(expected)
        or {row.get("file") for row in rows} != set(expected)
        or any(row.get("profile") != profile for row in rows)
        or any(not row.get("status") or row.get("error")
               or row["status"] == "COPIED_ORIGINAL_SVGO_FAILED" for row in rows)
    ):
        raise ValueError("Compression report does not cover every canonical SVG")


def compress_canonical_symbols(
    canonical_archive: Path,
    conversion_receipt: Path,
    output_archive: Path,
    output_receipt: Path,
    report: Path,
    candidates_report: Path,
    run_report: Path,
    *,
    profile: str = "balanced",
    renderer: str = "resvg",
    target_sizes: tuple[int, ...] = (32, 64),
    dprs: tuple[float, ...] = (1.0, 2.0),
    balanced_precisions: tuple[int, ...] = (2, 3),
    max_rms: float = 0.01,
    max_changed_fraction: float = 0.01,
    pixel_diff_threshold: int = 8,
    jobs: int = 2,
    svgo: str | None = None,
    resvg: str | None = None,
    inkscape: str | None = None,
) -> CanonicalCompressionResult:
    """Build canonical-only compressed ZIP after verifying all previous evidence."""
    destinations = (output_archive, output_receipt, report, candidates_report, run_report)
    if len({p.resolve() for p in destinations}) != len(destinations):
        raise ValueError("Compression outputs must be distinct")
    if any(p.exists() for p in destinations):
        raise FileExistsError("Canonical compression never overwrites an existing output")
    if profile not in {"balanced", "small"}:
        raise ValueError("Canonical publication compression requires a raster-validated profile")
    if renderer not in {"resvg", "inkscape"}:
        raise ValueError("Canonical compression requires an explicit renderer")
    if (
        jobs < 1 or not target_sizes or not dprs or not balanced_precisions
        or any(x <= 0 for x in target_sizes)
        or any(x <= 0 for x in dprs)
        or max_rms < 0 or max_changed_fraction < 0
        or not 0 <= pixel_diff_threshold <= 255
    ):
        raise ValueError("Invalid compression validation parameters")
    expected = _verify_input(canonical_archive, conversion_receipt)
    with tempfile.TemporaryDirectory(prefix=".canonical-compression-") as tmp:
        work = Path(tmp)
        source_root = work / "input"
        source_root.mkdir()
        with zipfile.ZipFile(canonical_archive) as archive:
            for path in sorted(expected):
                target = source_root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(path))
        result = svg_compress.compress_svg_tree(
            source_root, work / "compressed",
            profile=profile, target_sizes=target_sizes, dprs=dprs,
            balanced_precisions=balanced_precisions, max_rms=max_rms,
            max_changed_fraction=max_changed_fraction,
            pixel_diff_threshold=pixel_diff_threshold, jobs=jobs,
            renderer=renderer, svgo=svgo, resvg=resvg, inkscape=inkscape,
        )
        info = result.run_info
        if (
            info.get("renderer") != renderer
            or not info.get("renderer_version")
            or info.get("renderer_version") == "unknown"
            or not info.get("svgo_version")
            or info.get("svgo_version") == "unknown"
            or info.get("profiles") != [profile]
            or info.get("target_sizes_css_px") != list(target_sizes)
            or info.get("dprs") != list(dprs)
            or info.get("max_rms") != max_rms
            or info.get("max_changed_fraction") != max_changed_fraction
            or info.get("pixel_diff_threshold") != pixel_diff_threshold
        ):
            raise ValueError("Compression run lacks pinned renderer/SVGO versions or parameters")
        _read_compression_rows(result.report, expected, profile)
        with result.candidates_report.open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            candidates = list(csv.DictReader(stream))
        if any(row.get("error") for row in candidates):
            raise ValueError("Compression candidates contain unresolved tool/render errors")
        output_assets: list[dict[str, Any]] = []
        bodies: dict[str, bytes] = {}
        for path in sorted(expected):
            target = result.profile_root / path
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"Compression output missing: {path}")
            body = target.read_bytes()
            scan = _scan_svg_bytes(path, body)
            if scan["parseStatus"] != "ok" or scan["activeText"]:
                raise ValueError(f"Compression output invalid or contains active text: {path}")
            bodies[path] = body
            output_assets.append({
                "archivePath": path,
                "sha256": hashlib.sha256(body).hexdigest(),
                "sourceSha256": expected[path],
            })
        actual = {p.relative_to(result.profile_root).as_posix()
                  for p in result.profile_root.rglob("*") if p.is_file()}
        if actual != set(expected):
            raise ValueError("Unexpected compressed output members")
        report_bytes = [
            result.report.read_bytes(), result.candidates_report.read_bytes(),
            result.run_report.read_bytes(),
        ]
        for path in destinations:
            path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(output_archive, "x") as archive:
                for path in sorted(bodies):
                    archive.writestr(_archive_info(path), bodies[path])
            receipt = {
                "format": STAGE_RECEIPT_FORMAT,
                "formatVersion": STAGE_RECEIPT_VERSION,
                "stage": "compression",
                "status": "passed",
                "inputArtifact": _pin(canonical_archive),
                "textConversionReceipt": _pin(conversion_receipt),
                "profile": profile,
                "renderer": renderer,
                "rendererVersion": info["renderer_version"],
                "svgoVersion": info["svgo_version"],
                "parameters": {
                    "targetSizes": list(target_sizes),
                    "dprs": list(dprs),
                    "balancedPrecisions": list(balanced_precisions),
                    "maxRms": max_rms,
                    "maxChangedFraction": max_changed_fraction,
                    "pixelDiffThreshold": pixel_diff_threshold,
                    "jobs": jobs,
                },
                "reports": {
                    name: {"name": path.name, "sha256": hashlib.sha256(data).hexdigest()}
                    for name, path, data in zip(
                        ("compression", "candidates", "run"),
                        (report, candidates_report, run_report), report_bytes,
                    )
                },
                "outputArtifact": _pin(output_archive),
                "outputAssets": output_assets,
                "summary": result.summary,
            }
            _publish_reports([
                (report, report_bytes[0]),
                (candidates_report, report_bytes[1]),
                (run_report, report_bytes[2]),
                (output_receipt, _canonical(receipt)),
            ])
        except BaseException:
            output_archive.unlink(missing_ok=True)
            raise
    return CanonicalCompressionResult(
        "passed", output_archive, output_receipt, report, candidates_report,
        run_report, result.summary,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for field in (
        "canonical-archive", "conversion-receipt", "output-archive",
        "receipt", "report", "candidates-report", "run-report",
    ):
        parser.add_argument("--" + field, type=Path, required=True)
    parser.add_argument("--profile", choices=["balanced", "small"], default="balanced")
    parser.add_argument("--renderer", choices=["resvg", "inkscape"], default="resvg")
    parser.add_argument("--target-sizes", default="32,64")
    parser.add_argument("--dprs", default="1,2")
    parser.add_argument("--balanced-precisions", default="2,3")
    parser.add_argument("--max-rms", type=float, default=0.01)
    parser.add_argument("--max-changed", type=float, default=0.01)
    parser.add_argument("--pixel-diff-threshold", type=int, default=8)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--svgo")
    parser.add_argument("--resvg")
    parser.add_argument("--inkscape")
    args = parser.parse_args(argv)
    try:
        result = compress_canonical_symbols(
            args.canonical_archive, args.conversion_receipt,
            args.output_archive, args.receipt, args.report,
            args.candidates_report, args.run_report,
            profile=args.profile, renderer=args.renderer,
            target_sizes=tuple(int(x) for x in args.target_sizes.split(",")),
            dprs=tuple(float(x) for x in args.dprs.split(",")),
            balanced_precisions=tuple(int(x) for x in args.balanced_precisions.split(",")),
            max_rms=args.max_rms, max_changed_fraction=args.max_changed,
            pixel_diff_threshold=args.pixel_diff_threshold, jobs=args.jobs,
            svgo=args.svgo, resvg=args.resvg, inkscape=args.inkscape,
        )
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(f"Canonical compression {result.status}: {result.output_archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
