"""Read-only validation of a pinned raw-symbol acquisition request plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.raw_acquisition import acquire_raw_symbols
from infinity_symbol_tools.source_plan import SourcePlanError, load_source_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--army", required=True, type=Path)
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="Explicitly fetch raw SVGs; validation alone never accesses the network",
    )
    parser.add_argument(
        "--destination", type=Path, default=Path("data/raw/symbols"),
    )
    parser.add_argument(
        "--manifest-dir", type=Path, default=Path("data/manifests/snapshots"),
    )
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args(argv)
    try:
        plan = load_source_plan(args.plan, army_archive=args.army)
        if args.acquire:
            result = acquire_raw_symbols(
                plan,
                args.army,
                args.destination,
                args.manifest_dir,
                delay=args.delay,
            )
    except (OSError, ValueError) as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    if args.acquire:
        print(
            f"Acquired {result.downloaded} raw SVGs; "
            f"{result.unavailable} unavailable HTTP 404. "
            f"Archive: {result.archive}; provenance: {result.snapshot_manifest}; "
            f"report: {result.report}"
        )
    else:
        print(
            f"Verified {len(plan['requests'])} explicit source requests "
            f"bound to {args.army.name} (no network access)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
