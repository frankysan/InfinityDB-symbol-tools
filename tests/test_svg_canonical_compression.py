"""Receipt-chained canonical compression boundaries, without external binaries."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from infinity_symbol_tools import svg_compress
from infinity_symbol_tools.snapshot_archive import _archive_info
from infinity_symbol_tools.snapshot_provenance import sha256_file
from infinity_symbol_tools.svg_canonical_compression import compress_canonical_symbols
from infinity_symbol_tools.svg_preflight import STAGE_RECEIPT_FORMAT, STAGE_RECEIPT_VERSION

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


def _inputs(root: Path):
    source = root / "canonical.zip"
    with zipfile.ZipFile(source, "x") as archive:
        archive.writestr(_archive_info("units/a.svg"), SVG)
    import hashlib
    digest = hashlib.sha256(SVG).hexdigest()
    receipt = root / "text.json"
    receipt.write_text(json.dumps({
        "format": STAGE_RECEIPT_FORMAT,
        "formatVersion": STAGE_RECEIPT_VERSION,
        "stage": "textConversion",
        "status": "passed",
        "outputArtifact": {"name": source.name, "sha256": sha256_file(source)},
        "outputAssets": [{"archivePath": "units/a.svg", "sha256": digest}],
        "canonicalByArchivePath": {"units/a.svg": "units/a.svg"},
    }), encoding="utf-8")
    return source, receipt


def _run(root: Path, source: Path, receipt: Path):
    return compress_canonical_symbols(
        source, receipt, root / "compressed.zip", root / "compression.json",
        root / "compression.csv", root / "candidates.csv", root / "run.json",
    )


def _mock_compressor(monkeypatch, *, omit_output=False, wrong_version=False,
                     failed_status=False):
    def fake_compress(input_path, output_root, **kwargs):
        assert kwargs["profile"] == "balanced"
        assert kwargs["renderer"] == "resvg"
        output = output_root / "balanced" / "units" / "a.svg"
        output.parent.mkdir(parents=True)
        if not omit_output:
            output.write_bytes(SVG)
        reports = output_root / "reports"
        reports.mkdir()
        row = svg_compress.OutputResult(
            file="units/a.svg", profile="balanced",
            status="COPIED_ORIGINAL_SVGO_FAILED" if failed_status else "UNCHANGED",
            source_bytes=len(SVG), output_bytes=len(SVG),
            gzip_bytes=20, reduction_percent=0.0, chosen_candidate="original",
            precision="", simplify_passes=0, worst_rms=0.0,
            worst_changed_fraction=0.0,
        )
        info = {
            "renderer": "resvg",
            "renderer_version": None if wrong_version else "resvg-test",
            "svgo_version": "svgo-test",
            "profiles": ["balanced"],
            "target_sizes_css_px": [32, 64], "dprs": [1.0, 2.0],
            "max_rms": 0.01, "max_changed_fraction": 0.01,
            "pixel_diff_threshold": 8,
        }
        svg_compress.write_reports(reports, [row], [], info)
        return svg_compress.CompressionRunResult(
            output_root=output_root, profile_root=output_root / "balanced",
            report=reports / "compression-report.csv",
            candidates_report=reports / "compression-candidates.csv",
            run_report=reports / "compression-run.json",
            summary={"assetCount": 1, "compressedAssetCount": 0},
            run_info=info,
        )
    monkeypatch.setattr(svg_compress, "compress_svg_tree", fake_compress)


def test_compression_receipt_pins_versions_reports_and_output(tmp_path, monkeypatch):
    source, receipt = _inputs(tmp_path)
    _mock_compressor(monkeypatch)
    result = _run(tmp_path, source, receipt)
    assert result.status == "passed"
    assert result.output_archive.is_file()
    with zipfile.ZipFile(result.output_archive) as archive:
        assert archive.namelist() == ["units/a.svg"]
        assert archive.read("units/a.svg") == SVG
    state = json.loads(result.receipt.read_text())
    assert state["stage"] == "compression"
    assert state["rendererVersion"] == "resvg-test"
    assert state["svgoVersion"] == "svgo-test"
    assert state["textConversionReceipt"]["sha256"] == sha256_file(receipt)
    assert state["outputArtifact"]["sha256"] == sha256_file(result.output_archive)
    assert state["outputAssets"][0]["sourceSha256"] == state["outputAssets"][0]["sha256"]


@pytest.mark.parametrize("mode", ["missing", "version", "fallback"])
def test_failed_compression_never_publishes(tmp_path, monkeypatch, mode):
    source, receipt = _inputs(tmp_path)
    _mock_compressor(monkeypatch, omit_output=mode == "missing",
                     wrong_version=mode == "version",
                     failed_status=mode == "fallback")
    with pytest.raises(ValueError):
        _run(tmp_path, source, receipt)
    assert not (tmp_path / "compressed.zip").exists()
    assert not (tmp_path / "compression.json").exists()


def test_tampered_input_and_unsafe_profile_rejected(tmp_path, monkeypatch):
    source, receipt = _inputs(tmp_path)
    state = json.loads(receipt.read_text())
    state["outputAssets"][0]["sha256"] = "0" * 64
    receipt.write_text(json.dumps(state), encoding="utf-8")
    _mock_compressor(monkeypatch)
    with pytest.raises(ValueError, match="hash mismatch"):
        _run(tmp_path, source, receipt)
    with pytest.raises(ValueError, match="raster-validated"):
        compress_canonical_symbols(
            source, receipt, tmp_path / "a.zip", tmp_path / "a.json",
            tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "b.json",
            profile="lossless",
        )


def test_output_is_immutable(tmp_path, monkeypatch):
    source, receipt = _inputs(tmp_path)
    _mock_compressor(monkeypatch)
    _run(tmp_path, source, receipt)
    with pytest.raises(FileExistsError):
        _run(tmp_path, source, receipt)
