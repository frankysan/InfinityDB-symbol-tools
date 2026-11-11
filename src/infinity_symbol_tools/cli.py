"""Read-only validation of a pinned raw-symbol acquisition request plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from infinity_symbol_tools.source_plan import SourcePlanError, load_source_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--army", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = load_source_plan(args.plan, army_archive=args.army)
    except SourcePlanError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    print(
        f"Verified {len(plan['requests'])} explicit source requests "
        f"bound to {args.army.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
