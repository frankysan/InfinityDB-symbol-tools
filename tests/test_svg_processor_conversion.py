from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

from infinity_symbol_tools import svg_processor


def _source_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<text style="font-family: sans-serif">X</text></svg>'
    )


def _path_svg() -> str:
    return '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


def test_convert_available_svgs_uses_persistent_shell_and_portable_report_paths(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / "work"
    source = output_root / "fonts_available" / "nested" / "text.svg"
    source.parent.mkdir(parents=True)
    source.write_text(_source_svg(), encoding="utf-8")

    monkeypatch.setattr(
        svg_processor,
        "normalize_svg_for_conversion",
        lambda source, target, _exact, _compact: (
            shutil.copy2(source, target)
            and {"aliases_normalized": "", "warnings": ""}
        ),
    )
    monkeypatch.setattr(
        svg_processor,
        "resolve_text_converter",
        lambda requested: ("inkscape-shell", "inkscape-test"),
    )
    monkeypatch.setattr(
        svg_processor,
        "executable_version",
        lambda executable: f"{executable} 1.0",
    )

    class FakeShell:
        def __init__(self, executable: str):
            assert executable == "inkscape-test"

        def convert_text_to_path(self, _source: Path, destination: Path):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(_path_svg(), encoding="utf-8")
            return subprocess.CompletedProcess([], 0, "", "")

        def close(self) -> None:
            pass

    monkeypatch.setattr(svg_processor, "InkscapeShellWorker", FakeShell)

    result = svg_processor.convert_available_svgs(
        output_root,
        {},
        {},
        jobs=1,
        text_converter="inkscape-shell",
    )

    assert result["converted"] == 1
    assert result["failed"] == 0
    assert result["converter"] == "inkscape-shell"
    assert result["converter_version"] == "inkscape-test 1.0"
    assert (output_root / "text_as_paths" / "nested" / "text.svg").is_file()

    with result["report_path"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["file"] == "nested/text.svg"
    assert rows[0]["status"] == "CONVERTED"


def test_convert_available_svgs_with_no_candidates_does_not_require_converter(
    tmp_path: Path, monkeypatch
) -> None:
    output_root = tmp_path / "work"
    (output_root / "fonts_available").mkdir(parents=True)

    def fail_resolve(_requested: str):
        raise AssertionError("converter must not be resolved for an empty candidate set")

    monkeypatch.setattr(svg_processor, "resolve_text_converter", fail_resolve)

    result = svg_processor.convert_available_svgs(
        output_root,
        {},
        {},
        jobs=4,
        text_converter="inkscape-shell",
    )

    assert result["converted"] == 0
    assert result["failed"] == 0
    assert result["converter"] == "inkscape-shell"
    assert result["converter_version"] == ""


def test_normalize_svg_removes_only_unresolved_local_images(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "present.png").write_bytes(b"present")
    source = source_dir / "symbol.svg"
    source.write_text(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink">'
            '<image id="missing-href" href="missing.png"/>'
            '<image id="missing-xlink" xlink:href="missing-xlink.png"/>'
            '<image id="present" href="present.png"/>'
            '<image id="embedded" href="data:image/png;base64,AAAA"/>'
            '<image id="fragment" href="#paint"/>'
            '<image id="remote" href="https://example.com/image.png"/>'
            '</svg>'
        ),
        encoding="utf-8",
    )

    targets = [
        tmp_path / "temp-a" / "symbol.svg",
        tmp_path / "temp-b" / "symbol.svg",
    ]
    results = [
        svg_processor.normalize_svg_for_conversion(source, target, {}, {})
        for target in targets
    ]

    for target in targets:
        text = target.read_text(encoding="utf-8")
        assert "missing.png" not in text
        assert "missing-xlink.png" not in text
        assert "present.png" in text
        assert "data:image/png;base64,AAAA" in text
        assert "#paint" in text
        assert "https://example.com/image.png" in text

    expected_warning = (
        "Removed 2 unresolved external image reference(s): "
        "missing.png, missing-xlink.png"
    )
    assert [result["warnings"] for result in results] == [
        expected_warning,
        expected_warning,
    ]
    assert targets[0].read_bytes() == targets[1].read_bytes()
    assert "missing.png" in source.read_text(encoding="utf-8")
