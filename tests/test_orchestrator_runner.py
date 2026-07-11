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
            with self.assertRaises(InsufficientEvidenceError):
                build_backtest_runner()
        finally:
            if old is not None:
                os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_no_arg_factory_loads_valid_preflight_bundle(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=3, freq="D")
            pd.DataFrame({
                "date": dates,
                "symbol": ["BTCUSDT"] * 3,
                "in_universe": [True] * 3,
                "open": [100.0, 101.0, 102.0],
                "final_score": [0.1, 0.2, 0.3],
            }).to_csv(root / "research_panel.csv.gz", index=False, compression="gzip")
            (root / "manifest.json").write_text(json.dumps({"contract_version": "crypto.lifecycle_preflight.v1", "strategy_profile": "crypto_live_pool_rotation"}), encoding="utf-8")
            old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
            os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
            try:
                self.assertIsNotNone(build_backtest_runner())
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
