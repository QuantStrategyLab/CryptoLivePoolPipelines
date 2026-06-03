#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.pipeline import run_research_pipeline
from src.utils import get_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full crypto live pool rotation research pipeline.")
    parser.add_argument("--config", default="config/default.yaml", help="Path to the YAML config file.")
    parser.add_argument("--universe-mode", default=None, help="Optional universe mode override, e.g. broad_liquid.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger = get_logger("run_research_backtest")
    config = load_config(args.config)
    result = run_research_pipeline(config, universe_mode=args.universe_mode)

    logger.info("Research pipeline finished.")
    logger.info("Universe mode: %s", result["universe_mode"])
    logger.info("Performance summary:\n%s", result["performance_table"].to_string(index=False))
    logger.info("Leader metrics:\n%s", result["leader_metrics"].to_string(index=False))
    logger.info("Reports saved under %s", config["paths"].reports_dir)


if __name__ == "__main__":
    main()
