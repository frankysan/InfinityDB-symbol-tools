import json
from pathlib import Path

import pytest

from infinity_symbol_tools import svg_processor


def test_tracked_font_alias_config_contains_known_infinity_overrides() -> None:
    overrides = svg_processor.load_font_reference_overrides()

    assert overrides["jura-bold"] == {
        "lookup": "Jura",
        "subfamily": "Bold",
        "weight": "700",
    }
    assert overrides["bank gothic bt"] == {"lookup": "BankGothicBT-Medium"}


def test_font_alias_config_rejects_duplicate_normalized_reference(tmp_path: Path) -> None:
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "overrides": [
                    {"reference": "Jura-Bold", "lookup": "Jura"},
                    {"reference": "  jura-bold ", "lookup": "Other"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicates another alias"):
        svg_processor.load_font_reference_overrides(path)


def test_font_alias_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "aliases.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "overrides": [
                    {
                        "reference": "Legacy",
                        "lookup": "Installed",
                        "surprise": "not allowed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown field"):
        svg_processor.load_font_reference_overrides(path)
