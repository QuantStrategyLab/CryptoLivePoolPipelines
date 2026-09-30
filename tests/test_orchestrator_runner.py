from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from src.backtest import run_single_backtest  # noqa: E402
from src.strategy_lifecycle.orchestrator_runner import (  # noqa: E402
    DEFAULT_BACKTEST_CONFIG,
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)


class CryptoOrchestratorRunnerTests(unittest.TestCase):
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

    def test_supported_top_n_changes_executed_config(self) -> None:
        dates = pd.date_range("2024-01-01", periods=6)
        index = pd.MultiIndex.from_product([dates, ("A", "B")], names=["date", "symbol"])
        panel = pd.DataFrame(index=index)
        panel["in_universe"] = True
        panel["final_score"] = [1.0, 0.0] * len(dates)
        panel["open"] = [100.0, 100.0, 100.0, 100.0, 150.0, 100.0, 150.0, 100.0, 150.0, 100.0, 150.0, 100.0]
        top_one = {"strategy": {**DEFAULT_BACKTEST_CONFIG["strategy"], "top_n": 1}}
        top_two = {"strategy": {**DEFAULT_BACKTEST_CONFIG["strategy"], "top_n": 2}}
        direct_top_one = run_single_backtest(panel, "final_score", top_one)
        direct_top_two = run_single_backtest(panel, "final_score", top_two)
        self.assertNotAlmostEqual(direct_top_one.metrics["CAGR"], direct_top_two.metrics["CAGR"])

        result = CryptoLivePoolBacktestRunner(panel=panel).run(PROFILE_NAME, {"top_n": 1})
        self.assertEqual(result.params["top_n"], 1)
        self.assertAlmostEqual(result.cagr, direct_top_one.metrics["CAGR"])

    def test_observation_count_uses_actual_return_window(self) -> None:
        dates = pd.date_range("2024-01-01", periods=21)
        index = pd.MultiIndex.from_product([dates, ("A", "B")], names=["date", "symbol"])
        panel = pd.DataFrame({"in_universe": True, "final_score": 1.0, "open": 100.0}, index=index)
        panel["prediction_window_count"] = 1
        panel.loc[(dates[0], slice(None)), "final_score"] = pd.NA
        panel.loc[(dates[0], slice(None)), "prediction_window_count"] = 0
        direct = run_single_backtest(panel, "final_score", DEFAULT_BACKTEST_CONFIG)
        self.assertGreater(len(direct.returns), 0)
        self.assertGreater(direct.returns.index.min(), dates[0])

        result = CryptoLivePoolBacktestRunner(panel=panel).run(PROFILE_NAME, {})
        self.assertEqual(result.observation_count, len(direct.returns))
        self.assertEqual(result.start_date, direct.returns.index.min().date())
        self.assertEqual(result.end_date, direct.returns.index.max().date())

    def test_insufficient_window_does_not_return_a_normal_result(self) -> None:
        dates = pd.date_range("2024-01-01", periods=1)
        index = pd.MultiIndex.from_product([dates, ("A",)], names=["date", "symbol"])
        panel = pd.DataFrame({"in_universe": True, "final_score": 1.0, "open": 100.0}, index=index)
        with self.assertRaisesRegex(ValueError, "Insufficient"):
            CryptoLivePoolBacktestRunner(panel=panel).run(PROFILE_NAME, {})

    def test_unsupported_execution_params_are_rejected(self) -> None:
        dates = pd.date_range("2024-01-01", periods=6)
        index = pd.MultiIndex.from_product([dates, ("A",)], names=["date", "symbol"])
        panel = pd.DataFrame({"in_universe": True, "final_score": 1.0, "open": 100.0}, index=index)
        with self.assertRaisesRegex(ValueError, "Unsupported execution params"):
            CryptoLivePoolBacktestRunner(panel=panel).run(PROFILE_NAME, {"top_n": 99, "not_a_strategy_field": 1})


if __name__ == "__main__":
    unittest.main()
