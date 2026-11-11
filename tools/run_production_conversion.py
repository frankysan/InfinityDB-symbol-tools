"""Convert canonical production SVG text after passed duplicate detection."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.svg_text_conversion import main as conversion_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-stem", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    stem = args.effective_stem
    if not stem.startswith("EFFECTIVE-SYMBOLS ") or any(
        item in stem for item in ("/", "\\", "..")
    ):
        parser.error("Expected a safe EFFECTIVE-SYMBOLS timestamp stem")
    root = Path("local/p5-production-effective")
    reports = Path("local/p5-production-reports") / stem
    output = Path("local/p5-production-processed") / stem
    if not (reports / "duplicate-state.json").is_file():
        parser.error("Duplicate receipt is required")
    return conversion_main([
        "--effective-archive", str(root / f"{stem}.zip"),
        "--effective-manifest", str(root / f"{stem}.json"),
        "--effective-report", str(root / f"{stem}.effective-report.json"),
        "--font-report", str(reports / "font-audit.json"),
        "--font-receipt", str(reports / "font-audit-state.json"),
        "--duplicate-receipt", str(reports / "duplicate-state.json"),
        "--duplicate-groups", str(reports / "duplicates/duplicate-groups.csv"),
        "--duplicate-errors", str(reports / "duplicates/duplicate-render-errors.csv"),
        "--duplicate-summary", str(reports / "duplicates/duplicate-summary.csv"),
        "--output-archive", str(output / "CANONICAL.zip"),
        "--conversion-report", str(output / "svg-text-to-path-report.csv"),
        "--conversion-summary", str(output / "text-conversion-summary.csv"),
        "--receipt", str(output / "text-conversion-state.json"),
        "--converter", "inkscape-shell",
        "--jobs", str(args.jobs),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
