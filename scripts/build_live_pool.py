#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.pipeline import build_live_pool_outputs
from src.release_contract import assert_release_outputs
from src.utils import get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the latest live universe, ranking, and pool exports. Defaults to Production v1 (Binance-only + core_major)."
    )
    parser.add_argument("--config", default="config/default.yaml", help="Path to the YAML config file.")
    parser.add_argument("--as-of-date", default=None, help="Optional historical snapshot date for live build.")
    parser.add_argument("--universe-mode", default=None, help="Optional universe mode override, e.g. core_major.")
    parser.add_argument(
        "--contract-max-age-days",
        type=int,
        default=45,
        help="Maximum allowed output age when validating the default latest production build.",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow older historical outputs to pass contract validation without freshness errors.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    as_of_date = pd.Timestamp(args.as_of_date) if args.as_of_date else None
    logger = get_logger("build_live_pool")

    result = build_live_pool_outputs(config, as_of_date=as_of_date, universe_mode=args.universe_mode)
    logger.info("Live pool built for %s", result["as_of_date"].date())
    logger.info(
        "Universe mode: %s | Training window: %s -> %s | linear=%s | ml=%s",
        result["universe_mode"],
        result["train_start_date"].date(),
        result["train_end_date"].date(),
        result["linear_backend"],
        result["ml_backend"],
    )
    validation = assert_release_outputs(
        config["paths"].output_dir,
        expected_mode=result["universe_mode"],
        expected_source_project=str(
            config.get("publish", {}).get("source_project", config.get("project", {}).get("name", "crypto-live-pool-pipelines"))
        ),
        expected_pool_size=int(config["export"]["live_pool_size"]),
        max_age_days=args.contract_max_age_days,
        require_artifact_manifest=True,
        require_freshness=not bool(args.as_of_date or args.allow_stale),
    )
    logger.info(
        "Release contract validated | version=%s | pool_size=%s | release_manifest_present=%s | artifact_manifest_present=%s",
        validation["version"],
        validation["pool_size"],
        validation["manifest_present"],
        validation["artifact_manifest_present"],
    )
    logger.info("Export payload:\n%s", result["live_payload"])


if __name__ == "__main__":
    main()
