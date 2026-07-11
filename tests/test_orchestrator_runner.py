from __future__ import annotations

import sys
import tempfile
import unittest
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.strategy_lifecycle.orchestrator_runner import (  # noqa: E402
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)


class CryptoOrchestratorRunnerTests(unittest.TestCase):
    def test_production_wrapper_requires_real_panel(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        self.assertIsNotNone(build_backtest_runner)
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError

        old = os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
        try:
            runner = build_backtest_runner()
            with self.assertRaises(InsufficientEvidenceError):
                runner.run(PROFILE_NAME, {})
        finally:
            if old is not None:
                os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_no_arg_factory_loads_valid_preflight_bundle(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from src.strategy_lifecycle.orchestrator_runner import _synthetic_panel
            panel = _synthetic_panel(days=150).reset_index()
            panel["date"] = pd.to_datetime(panel["date"])
            panel["date"] += pd.Timestamp.today().normalize() - panel["date"].max()
            panel.to_csv(root / "research_panel.csv.gz", index=False, compression="gzip")
            panel[["date", "symbol", "open"]].rename(columns={"open": "close"}).to_csv(root / "market_history.csv.gz", index=False, compression="gzip")
            (root / "manifest.json").write_text(json.dumps({
                "contract_version": "crypto.lifecycle_preflight.v1",
                "producer": "export_lifecycle_preflight_inputs.py",
                "strategy_profile": "crypto_live_pool_rotation",
                "panel_rows": len(panel),
                "panel_symbols": sorted(panel["symbol"].unique().tolist()),
                "market_rows": len(panel),
                "market_symbols": sorted(panel["symbol"].unique().tolist()),
            }), encoding="utf-8")
            old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
            os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
            try:
                runner = build_backtest_runner()
                self.assertIsNotNone(runner)
                self.assertEqual(runner._runner, None)
                result = runner.run(PROFILE_NAME, {})
                self.assertEqual(result.strategy_profile, PROFILE_NAME)
                self.assertIsNotNone(runner._runner)
            finally:
                if old is None:
                    os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
                else:
                    os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_supported_profile(self) -> None:
        self.assertIn(PROFILE_NAME, SUPPORTED_PROFILES)

    def test_run_returns_backtest_result(self) -> None:
        runner = CryptoLivePoolBacktestRunner(synthetic_days=1600)
        result = runner.run(
            PROFILE_NAME,
            {},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 3, 1),
        )
        self.assertEqual(result.strategy_profile, PROFILE_NAME)
        self.assertEqual(result.domain, "crypto")
        self.assertIsNotNone(result.sharpe_ratio)

    def test_walk_forward_produces_one_result_per_window(self) -> None:
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner("crypto", CryptoLivePoolBacktestRunner(synthetic_days=1600))
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                PROFILE_NAME,
                domain="crypto",
                params={},
                windows=windows,
            )
            self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
