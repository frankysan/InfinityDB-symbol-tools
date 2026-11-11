from __future__ import annotations

from pathlib import Path

import infinity_symbol_tools.svg_compress as svg_compress


def test_compress_svg_tree_returns_reports_and_aggregate_sizes(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = tmp_path / "input"
    source = source_root / "units" / "example.svg"
    source.parent.mkdir(parents=True)
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>',
        encoding="utf-8",
    )
    output_root = tmp_path / "output"

    def fake_main(argv: list[str] | None = None) -> int:
        assert argv is not None
        assert "--profile" in argv
        assert argv[argv.index("--profile") + 1] == "balanced"
        assert argv[argv.index("--target-sizes") + 1] == "32,64"
        assert argv[argv.index("--dprs") + 1] == "1,2"
        assert argv[argv.index("--balanced-precisions") + 1] == "2,3"
        profile_root = output_root / "balanced" / "units"
        profile_root.mkdir(parents=True)
        compressed = profile_root / "example.svg"
        compressed.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        row = svg_compress.OutputResult(
            file="units/example.svg",
            profile="balanced",
            status="OK",
            source_bytes=source.stat().st_size,
            output_bytes=compressed.stat().st_size,
            gzip_bytes=10,
            reduction_percent=svg_compress.safe_reduction(
                source.stat().st_size, compressed.stat().st_size
            ),
            chosen_candidate="p2",
            precision="2",
            simplify_passes=0,
            worst_rms=0.0,
            worst_changed_fraction=0.0,
        )
        svg_compress.write_reports(
            output_root / "reports",
            [row],
            [],
            {"renderer": "resvg", "renderer_version": "resvg test"},
        )
        return 0

    monkeypatch.setattr(svg_compress, "main", fake_main)

    result = svg_compress.compress_svg_tree(source_root, output_root)

    assert result.summary["assetCount"] == 1
    assert result.summary["compressedAssetCount"] == 1
    assert result.summary["retainedAssetCount"] == 0
    assert result.summary["sourceBytes"] == source.stat().st_size
    assert result.summary["outputBytes"] < result.summary["sourceBytes"]
    assert result.summary["reclaimedBytes"] == (
        result.summary["sourceBytes"] - result.summary["outputBytes"]
    )
    assert result.run_info["renderer"] == "resvg"
    assert result.report.is_file()
    assert "units/example.svg" in result.report.read_text(encoding="utf-8-sig")


def test_compress_svg_tree_can_stream_cli_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source_root = tmp_path / "input"
    source_root.mkdir()
    source = source_root / "example.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    output_root = tmp_path / "output"

    def fake_main(argv: list[str] | None = None) -> int:
        assert argv is not None
        print("[1/1] example.svg (42 bytes)")
        profile_root = output_root / "balanced"
        profile_root.mkdir(parents=True)
        compressed = profile_root / "example.svg"
        compressed.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        row = svg_compress.OutputResult(
            file="example.svg",
            profile="balanced",
            status="UNCHANGED",
            source_bytes=source.stat().st_size,
            output_bytes=compressed.stat().st_size,
            gzip_bytes=10,
            reduction_percent=0.0,
            chosen_candidate="original",
            precision="",
            simplify_passes=0,
            worst_rms=0.0,
            worst_changed_fraction=0.0,
        )
        svg_compress.write_reports(
            output_root / "reports",
            [row],
            [],
            {"renderer": "resvg", "renderer_version": "resvg test"},
        )
        return 0

    monkeypatch.setattr(svg_compress, "main", fake_main)

    svg_compress.compress_svg_tree(source_root, output_root, stream_output=True)

    assert "[1/1] example.svg" in capsys.readouterr().out


def test_compress_svg_tree_empty_input_needs_no_external_tools(tmp_path: Path) -> None:
    source_root = tmp_path / "input"
    source_root.mkdir()
    output_root = tmp_path / "output"

    result = svg_compress.compress_svg_tree(source_root, output_root)

    assert result.summary == {
        "assetCount": 0,
        "compressedAssetCount": 0,
        "retainedAssetCount": 0,
        "sourceBytes": 0,
        "outputBytes": 0,
        "reclaimedBytes": 0,
        "reductionPercent": 0.0,
    }
    assert result.profile_root.is_dir()
    assert result.report.is_file()
    assert result.candidates_report.is_file()
    assert result.run_report.is_file()
