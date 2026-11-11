from pathlib import Path

import pytest

from infinity_symbol_tools import svg_processor

fonttools = pytest.importorskip("fontTools.ttLib")
pytest.importorskip("tinycss2")
pytest.importorskip("cssselect2")
pillow_image = pytest.importorskip("PIL.Image")


def test_real_fonttools_name_table_is_consumed() -> None:
    font = fonttools.TTFont()
    name_table = fonttools.newTable("name")
    name_table.names = []
    for value, name_id in (
        ("Integration Family", 1),
        ("Regular", 2),
        ("Integration Family", 4),
        ("IntegrationFamily-Regular", 6),
    ):
        name_table.setName(value, name_id, 3, 1, 0x409)
    font["name"] = name_table

    face = svg_processor.get_face(font, Path("integration.ttf"))

    assert face is not None
    assert face.family == "Integration Family"
    assert face.subfamily == "Regular"
    assert face.postscript_name == "IntegrationFamily-Regular"


def test_real_css_stack_resolves_svg_font_family(tmp_path: Path) -> None:
    svg = tmp_path / "styled-text.svg"
    svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg">
<style>.label { font-family: "Integration Family", sans-serif; }</style>
<text class="label">Hello</text>
</svg>
""",
        encoding="utf-8",
    )

    scan, error = svg_processor.scan_svg(svg)

    assert error is None
    assert scan is not None
    assert scan["found_active_text"] is True
    assert scan["text_runs"] == 1
    assert scan["used_fonts"] == {"Integration Family": 1, "sans-serif": 1}


def test_real_pillow_rgba_hash_is_pixel_based(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    pillow_image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(first)
    pillow_image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(second)

    assert svg_processor.rgba_pixel_hash(first) == svg_processor.rgba_pixel_hash(second)
