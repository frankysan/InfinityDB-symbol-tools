from pathlib import Path
from types import SimpleNamespace

import infinity_symbol_tools.svg_processor as svg_processor


def test_duplicate_detection_exact_first_visual_and_canonical_ranking(
    tmp_path: Path, monkeypatch
) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    exact = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'
    (input_root / "a.svg").write_text(exact, encoding="utf-8")
    (input_root / "b.svg").write_text(exact, encoding="utf-8")
    (input_root / "c.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0,0"/></svg>',
        encoding="utf-8",
    )
    (input_root / "d.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><path d="M1 1"/></svg>',
        encoding="utf-8",
    )

    monkeypatch.setattr(svg_processor, "pillow_image_module", lambda: object())
    monkeypatch.setattr(
        svg_processor, "resolve_duplicate_renderer", lambda _requested: ("resvg", "resvg")
    )
    monkeypatch.setattr(svg_processor, "executable_version", lambda _path: "test")

    def fake_render(_renderer, _executable, _source, destination, _size):
        destination.write_bytes(b"png")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(svg_processor, "render_svg_for_duplicate_check", fake_render)

    def fake_pixel_hash(path: Path) -> str:
        index = int(path.stem.rsplit("-", 1)[1])
        return "same" if index in {1, 2} else "different"

    monkeypatch.setattr(svg_processor, "rgba_pixel_hash", fake_pixel_hash)

    result = svg_processor.find_duplicate_svgs(
        input_root,
        output_root,
        {
            "a.svg": "fonts_available",
            "b.svg": "fonts_available",
            "c.svg": "no_active_text",
            "d.svg": "no_active_text",
        },
        jobs=1,
    )

    assert result["source_svg_files"] == 4
    assert result["canonical_svg_files"] == 2
    assert result["unique_byte_sets"] == 3
    assert result["renders_avoided_exact"] == 1
    assert result["exact_groups"] == 0
    assert result["visual_groups"] == 1
    assert result["redundant_files"] == 2
    assert result["duplicate_representatives"] == {
        "a.svg": "c.svg",
        "b.svg": "c.svg",
    }
    source_size = sum(path.stat().st_size for path in input_root.glob("*.svg"))
    canonical_size = (input_root / "c.svg").stat().st_size + (
        input_root / "d.svg"
    ).stat().st_size
    assert result["source_size_bytes"] == source_size
    assert result["canonical_size_bytes"] == canonical_size
    assert result["reclaimed_size_bytes"] == source_size - canonical_size
    assert result["reduction_percent"] == (
        (source_size - canonical_size) * 100.0 / source_size
    )
    summary = result["summary_path"].read_text(encoding="utf-8-sig")
    assert "source_size_bytes,canonical_size_bytes,reclaimed_size_bytes" in summary
    assert f"{source_size},{canonical_size},{source_size - canonical_size}" in summary
    report = result["report_path"].read_text(encoding="utf-8-sig")
    assert "representative,c.svg,no_active_text,c.svg" in report


def test_symbol_set_size_metrics_handles_empty_set(tmp_path: Path) -> None:
    result = svg_processor.symbol_set_size_metrics([], tmp_path, {})

    assert result == {
        "source_svg_files": 0,
        "canonical_svg_files": 0,
        "source_size_bytes": 0,
        "canonical_size_bytes": 0,
        "reclaimed_size_bytes": 0,
        "reduction_percent": 0.0,
    }
