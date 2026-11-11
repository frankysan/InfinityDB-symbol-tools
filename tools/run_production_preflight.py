"""Run pinned production SVG preflight and font audit (no publication)."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.svg_font_audit import main as font_main
from infinity_symbol_tools.svg_preflight import main as preflight_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--effective-stem", required=True)
    args = parser.parse_args(argv)
    stem = args.effective_stem
    if not stem.startswith("EFFECTIVE-SYMBOLS ") or any(
        item in stem for item in ("/", "\\", "..")
    ):
        parser.error("Expected a safe EFFECTIVE-SYMBOLS timestamp stem")
    root = Path("local/p5-production-effective")
    report_root = Path("local/p5-production-reports") / stem
    shared = [
        "--effective-archive", str(root / f"{stem}.zip"),
        "--effective-manifest", str(root / f"{stem}.json"),
        "--effective-report", str(root / f"{stem}.effective-report.json"),
    ]
    preflight = report_root / "svg-preflight.json"
    preflight_receipt = report_root / "svg-preflight-state.json"
    code = preflight_main(shared + [
        "--output", str(preflight),
        "--receipt", str(preflight_receipt),
    ])
    if code:
        return code
    return font_main(shared + [
        "--preflight-report", str(preflight),
        "--preflight-receipt", str(preflight_receipt),
        "--output", str(report_root / "font-audit.json"),
        "--receipt", str(report_root / "font-audit-state.json"),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
