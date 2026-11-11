"""Run the pinned P5 production override handoff without publishing app assets.

The source-side acquisition must already have completed. This command
validates its evidence and applies only the reviewed overrides.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.effective_processing import main as override_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-stem", required=True)
    args = parser.parse_args(argv)
    root = Path("local")
    raw = root / "p5-production-raw"
    stem = args.raw_stem
    if not stem.startswith("SYMBOLS ") or "/" in stem or "\\" in stem or ".." in stem:
        parser.error("Expected a safe SYMBOLS timestamp stem")
    paths = [
        raw / f"{stem}.zip",
        raw / f"{stem}.json",
        raw / f"{stem}.raw-report.json",
    ]
    if not all(path.is_file() for path in paths):
        parser.error("Raw acquisition ZIP, manifest and report are all required")
    return override_main([
        "--source-plan", str(root / "p5-production-input/source-plan.json"),
        "--override-lookups", str(root / "p5-production-input/override-lookups.json"),
        "--army", str(root / "p5-production-input/JSON 20260929-114335.zip"),
        "--raw-archive", str(paths[0]),
        "--raw-manifest", str(paths[1]),
        "--raw-report", str(paths[2]),
        "--overrides", str(root / "image_overrides"),
        "--destination", str(root / "p5-production-effective"),
        "--manifest-dir", str(root / "p5-production-effective"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
