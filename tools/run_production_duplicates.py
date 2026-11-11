"""Run the pinned production duplicate stage after a passed font audit."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.svg_duplicates import main as duplicate_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-stem", required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    stem = args.effective_stem
    if not stem.startswith("EFFECTIVE-SYMBOLS ") or any(
        item in stem for item in ("/", "\\", "..")
    ):
        parser.error("Expected a safe EFFECTIVE-SYMBOLS timestamp stem")
    root = Path("local/p5-production-effective")
    reports = Path("local/p5-production-reports") / stem
    if not (reports / "font-audit-state.json").is_file():
        parser.error("Passed font audit receipt required")
    return duplicate_main([
        "--effective-archive", str(root / f"{stem}.zip"),
        "--effective-manifest", str(root / f"{stem}.json"),
        "--effective-report", str(root / f"{stem}.effective-report.json"),
        "--font-report", str(reports / "font-audit.json"),
        "--font-receipt", str(reports / "font-audit-state.json"),
        "--reports-directory", str(reports / "duplicates"),
        "--receipt", str(reports / "duplicate-state.json"),
        "--renderer", "resvg",
        "--jobs", str(args.jobs),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
