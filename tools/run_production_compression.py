"""Compress pinned canonical production SVGs without app publication."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.svg_canonical_compression import main as compression_main


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
    output = Path("local/p5-production-processed") / stem
    if not (output / "text-conversion-state.json").is_file():
        parser.error("Text conversion receipt is required")
    return compression_main([
        "--canonical-archive", str(output / "CANONICAL.zip"),
        "--conversion-receipt", str(output / "text-conversion-state.json"),
        "--output-archive", str(output / "COMPRESSED.zip"),
        "--receipt", str(output / "compression-state.json"),
        "--report", str(output / "compression-report.csv"),
        "--candidates-report", str(output / "compression-candidates.csv"),
        "--run-report", str(output / "compression-run.json"),
        "--profile", "balanced",
        "--renderer", "resvg",
        "--jobs", str(args.jobs),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
