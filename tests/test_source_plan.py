"""No-network acceptance tests for the versioned raw-symbol request boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from infinity_symbol_tools.cli import main
from infinity_symbol_tools.source_plan import (
    FORMAT,
    VERSION,
    SourcePlanError,
    load_source_plan,
    validate_source_plan,
)

BASE = "https://assets.corvusbelli.net/army/img/"


def _plan(army: Path) -> dict:
    return {
        "format": FORMAT,
        "formatVersion": VERSION,
        "inputArtifact": {
            "name": army.name,
            "sha256": hashlib.sha256(army.read_bytes()).hexdigest(),
        },
        "sourceDocumentCount": 2,
        "requests": [
            {
                "url": BASE + "icon/cube.svg",
                "archivePath": "static/characteristics/cube.svg",
            },
            {
                "url": BASE + "logo/units/unit.svg",
                "archivePath": "units/unit.svg",
            },
        ],
    }


def test_pinned_source_plan_validates_and_cli_is_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    army = tmp_path / "JSON 20260917-120000.zip"
    army.write_bytes(b"immutable test input")
    plan = _plan(army)
    assert validate_source_plan(plan, army_archive=army) == plan
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert load_source_plan(path, army_archive=army) == plan
    assert main(["--plan", str(path), "--army", str(army)]) == 0
    assert "Verified 2 explicit source requests" in capsys.readouterr().out


def test_changed_army_bytes_fail_closed(tmp_path: Path) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"before")
    plan = _plan(army)
    army.write_bytes(b"after")
    with pytest.raises(SourcePlanError, match="SHA-256"):
        validate_source_plan(plan, army_archive=army)


@pytest.mark.parametrize(
    "field,value",
    [
        ("formatVersion", True),
        ("formatVersion", 2),
        ("sourceDocumentCount", 0),
        ("sourceDocumentCount", "2"),
        ("requests", []),
    ],
)
def test_invalid_top_level_contract_fails(tmp_path: Path, field: str, value: object) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"input")
    plan = _plan(army)
    plan[field] = value
    with pytest.raises(SourcePlanError):
        validate_source_plan(plan)


@pytest.mark.parametrize(
    "url",
    [
        "http://assets.corvusbelli.net/army/img/logo/unit.svg",
        "https://example.com/army/img/logo/unit.svg",
        "https://assets.corvusbelli.net.evil.com/army/img/logo/unit.svg",
        "https://assets.corvusbelli.net/private/unit.svg",
        "https://assets.corvusbelli.net:443/army/img/logo/unit.svg",
    ],
)
def test_bad_symbol_hosts_and_schemes_are_rejected(tmp_path: Path, url: str) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"input")
    plan = _plan(army)
    plan["requests"][0]["url"] = url
    with pytest.raises(SourcePlanError, match="unsupported symbol URL"):
        validate_source_plan(plan)


@pytest.mark.parametrize(
    "archive_path",
    [
        "../secret.svg",
        "/absolute.svg",
        "units/../../escape.svg",
        "units\\escape.svg",
        "units//empty.svg",
        "units/invalid name.svg",
        "units/asset.png",
        "units/C:drive.svg",
    ],
)
def test_unsafe_archive_paths_are_rejected(
    tmp_path: Path, archive_path: str,
) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"input")
    plan = _plan(army)
    plan["requests"][0]["archivePath"] = archive_path
    with pytest.raises(SourcePlanError, match="unsafe archive path"):
        validate_source_plan(plan)


def test_casefold_collision_and_duplicate_url_fail(tmp_path: Path) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"input")
    plan = _plan(army)
    plan["requests"][1]["archivePath"] = "static/characteristics/CUBE.svg"
    with pytest.raises(SourcePlanError, match="unsafe archive path"):
        validate_source_plan(plan)
    plan = _plan(army)
    plan["requests"][1]["archivePath"] = "static/characteristics/cube.svg"
    with pytest.raises(SourcePlanError, match="Duplicate case-insensitive archive path"):
        validate_source_plan(plan)
    plan = _plan(army)
    plan["requests"][1]["url"] = plan["requests"][0]["url"]
    with pytest.raises(SourcePlanError, match="sorted by unique URL"):
        validate_source_plan(plan)


def test_unexpected_keys_rejected(tmp_path: Path) -> None:
    army = tmp_path / "army.zip"
    army.write_bytes(b"input")
    plan = _plan(army)
    plan["allowOtherHosts"] = True
    with pytest.raises(SourcePlanError, match="requires exactly"):
        validate_source_plan(plan)
