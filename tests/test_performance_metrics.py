from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from src.backtest import run_single_backtest
from src.evaluation import compute_performance_metrics
from src.strategy_lifecycle.orchestrator_runner import (
    DEFAULT_BACKTEST_CONFIG,
    PROFILE_NAME,
    CryptoLivePoolBacktestRunner,
    _metrics_to_qpk_result,
)


class PerformanceMetricsTests(unittest.TestCase):
    def test_drawdown_includes_initial_equity_without_adding_an_observation(self) -> None:
        for returns, expected in (([-0.1], -0.1), ([-0.1, 0], -0.1),
                                  ([-0.1, 0.1], -0.1), ([-0.1, -0.1], -0.19),
                                  ([0.1, -0.2], -0.2)):
            with self.subTest(returns=returns):
                metrics = compute_performance_metrics(pd.Series(returns), periods_per_year=1)
                self.assertAlmostEqual(metrics["Max Drawdown"], expected)
                expected_cagr = np.prod(1 + np.array(returns)) ** (1 / len(returns)) - 1
                self.assertAlmostEqual(metrics["CAGR"], expected_cagr)

    def test_sortino_uses_full_sample_target_zero_rms(self) -> None:
        for returns, expected in (([-0.02], -np.sqrt(365)),
                                  ([-0.02] * 3, -np.sqrt(365)),
                                  ([-0.02, 0.0], -np.sqrt(365 / 2)),
                                  ([-0.02, 0.01, 0.0], -np.sqrt(365 / 12))):
            with self.subTest(returns=returns):
                metrics = compute_performance_metrics(pd.Series(returns))
                self.assertAlmostEqual(metrics["Sortino"], expected)

    def test_no_downside_remains_undefined(self) -> None:
        for returns in ([0.0], [0.01, 0.02], [0.0, 0.01]):
            with self.subTest(returns=returns):
                metrics = compute_performance_metrics(pd.Series(returns))
                self.assertTrue(np.isnan(metrics["Sortino"]))
                self.assertTrue(np.isnan(metrics["Calmar"]))
                self.assertEqual(metrics["Max Drawdown"], 0.0)
                result = _metrics_to_qpk_result(
                    strategy_profile=PROFILE_NAME, params={}, metrics=metrics,
                    start_date=None, end_date=None, run_duration_seconds=0,
                )
                self.assertIsNone(result.calmar_ratio)
                self.assertIsNone(result.sortino_ratio)

    def test_adapter_keeps_negative_calmar(self) -> None:
        result = _metrics_to_qpk_result(
            strategy_profile=PROFILE_NAME, params={},
            metrics={"CAGR": -0.1, "Max Drawdown": -0.2},
            start_date=None, end_date=None, run_duration_seconds=0,
        )
        self.assertAlmostEqual(result.calmar_ratio, -0.5)

    def test_simulator_metrics_reach_actual_qpk_consumer_without_sign_loss(self) -> None:
        dates = pd.date_range("2024-01-01", periods=6)
        index = pd.MultiIndex.from_product([dates, ["A"]], names=["date", "symbol"])
        panel = pd.DataFrame({"in_universe": True, "final_score": 1.0,
                              "open": [100.0, 100.0, 90.0, 81.0, 81.0, 81.0]}, index=index)
        simulated = run_single_backtest(panel, "final_score", DEFAULT_BACKTEST_CONFIG)
        entry_return = 0.9 / 1.0015 - 1
        expected_drawdown = 0.81 / 1.0015 - 1
        np.testing.assert_allclose(simulated.returns, [0.0, entry_return, -0.1, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(simulated.metrics["Max Drawdown"], expected_drawdown)
        expected_sortino = ((entry_return - 0.1) / 6) / np.sqrt((entry_return ** 2 + 0.1 ** 2) / 6) * np.sqrt(365)
        self.assertAlmostEqual(simulated.metrics["Sortino"], expected_sortino)

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner("crypto", CryptoLivePoolBacktestRunner(panel=panel))
            result = orchestrator.run(PROFILE_NAME, domain="crypto", params={})
            persisted = orchestrator.run_latest(PROFILE_NAME, domain="crypto")
            self.assertIsNotNone(persisted)
            self.assertAlmostEqual(result.max_drawdown, expected_drawdown)
            self.assertLess(result.calmar_ratio, 0.0)
            self.assertAlmostEqual(result.calmar_ratio, simulated.metrics["Calmar"])
            self.assertAlmostEqual(persisted.calmar_ratio, result.calmar_ratio)
            self.assertAlmostEqual(persisted.max_drawdown, result.max_drawdown)


if __name__ == "__main__":
    unittest.main()
