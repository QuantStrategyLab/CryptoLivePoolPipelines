#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.release_contract import validate_release_outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the local production release artifacts before publish, rollback, or downstream sync."
    )
    parser.add_argument("--output-dir", default="data/output", help="Directory containing release artifacts.")
    parser.add_argument("--mode", default=None, help="Optional expected mode, for example core_major.")
    parser.add_argument("--source-project", default="crypto-live-pool-pipelines", help="Expected source_project value.")
    parser.add_argument("--expected-pool-size", type=int, default=None, help="Optional expected live pool size.")
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=45,
        help="Maximum allowed output age before the release is treated as stale.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow older historical artifacts to pass without freshness errors.",
    )
    parser.add_argument(
        "--require-manifest",
        action="store_true",
        help="Require release_manifest.json and validate it against the live pool contract.",
    )
    parser.add_argument(
        "--require-artifact-manifest",
        action="store_true",
        help="Require artifact_manifest.json and validate the profile-aware artifact contract.",
    )
    parser.add_argument(
        "--reference-date",
        default=None,
        help="Optional reference date for freshness checks, in YYYY-MM-DD format.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    validation = validate_release_outputs(
        args.output_dir,
        expected_mode=args.mode,
        expected_source_project=args.source_project,
        expected_pool_size=args.expected_pool_size,
        reference_date=args.reference_date,
        max_age_days=args.max_age_days,
        require_manifest=args.require_manifest,
        require_artifact_manifest=args.require_artifact_manifest,
        require_freshness=not args.allow_stale,
    )
    print(json.dumps(validation, ensure_ascii=False, indent=2))
    if not validation["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
