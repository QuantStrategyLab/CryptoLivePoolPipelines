#!/usr/bin/env python3
"""Pilot: run crypto_live_pool_rotation through BacktestOrchestrator.walk_forward()."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.strategy_lifecycle.orchestrator_runner import (  # noqa: E402
    CryptoLivePoolBacktestRunner,
    PROFILE_NAME,
)

DEFAULT_WINDOWS: tuple[tuple[date, date], ...] = (
    (date(2023, 6, 1), date(2024, 5, 31)),
    (date(2024, 6, 1), date(2025, 5, 31)),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crypto live pool walk-forward pilot")
    parser.add_argument("--output", type=Path, default=Path("crypto_live_pool_walk_forward_pilot.json"))
    parser.add_argument("--synthetic-days", type=int, default=400)
    args = parser.parse_args()

    from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
    from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

    runner = CryptoLivePoolBacktestRunner(synthetic_days=args.synthetic_days)
    params: dict[str, object] = {}
    store = PerformanceStore(local_root=args.output.parent / ".wf_store")
    orchestrator = BacktestOrchestrator(store=store)
    orchestrator.register_runner("crypto", runner)

    baseline = runner.run(PROFILE_NAME, params)
    results = orchestrator.walk_forward(
        PROFILE_NAME,
        domain="crypto",
        params=params,
        windows=DEFAULT_WINDOWS,
        param_set_id="crypto_live_pool_wf_pilot",
    )
    payload = {
        "profile": PROFILE_NAME,
        "baseline": {
            "sharpe_ratio": baseline.sharpe_ratio,
            "max_drawdown": baseline.max_drawdown,
            "cagr": baseline.cagr,
        },
        "windows": [
            {
                "start": item.start_date.isoformat() if item.start_date else None,
                "end": item.end_date.isoformat() if item.end_date else None,
                "sharpe_ratio": item.sharpe_ratio,
                "max_drawdown": item.max_drawdown,
                "cagr": item.cagr,
            }
            for item in results
        ],
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
