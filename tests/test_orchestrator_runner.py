from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

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
        from src.strategy_lifecycle.backtest_wrapper import CryptoBacktestRunner

        runner = CryptoBacktestRunner()
        with self.assertRaisesRegex(ValueError, "real prepared market panel"):
            runner.run("crypto_live_pool_rotation", {})

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
