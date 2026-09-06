from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.backtest import run_single_backtest


class BacktestAccountingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = pd.date_range("2024-01-01", periods=6)
        self.config = {"strategy": {
            "rebalance_frequency": "daily", "top_n": 1, "weighting": "equal",
            "signal_lag_days": 1, "fee_bps": 10, "slippage_bps": 5,
        }}

    def panel(self, symbols=("A",)) -> pd.DataFrame:
        index = pd.MultiIndex.from_product([self.dates, symbols], names=["date", "symbol"])
        return pd.DataFrame({"in_universe": True, "final_score": 1.0, "open": 100.0}, index=index)

    def test_cash_exit_and_reentry_follow_signal_lag_and_trade_log(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[1:3], "A"), "in_universe"] = False
        panel.loc[(self.dates[3:], "A"), "open"] = 200.0

        result = run_single_backtest(panel, "final_score", self.config)

        entry_notional = 1 / 1.0015
        np.testing.assert_allclose(result.returns, [0, entry_notional - 1, -0.0015, 0, entry_notional - 1, 0])
        np.testing.assert_allclose(result.equity_curve, (1 + result.returns).cumprod())
        self.assertEqual(result.trades["effective_date"].tolist(), list(self.dates[[1, 2, 4]]))
        self.assertEqual(result.trades["signal_date"].tolist(), list(self.dates[[0, 1, 3]]))
        np.testing.assert_allclose(result.trades["weight_change"], [entry_notional, -1.0, entry_notional])
        self.assertEqual(result.holdings["date"].tolist(), list(self.dates[[1, 4, 5]]))
        np.testing.assert_allclose(result.turnover, [0, entry_notional / 2, 0.5, 0, entry_notional / 2, 0])

    def test_full_replacement_charges_both_asset_sides(self) -> None:
        panel = self.panel(("A", "B"))
        panel.loc[(slice(None), "B"), "final_score"] = 0.5
        panel.loc[(self.dates[1:], "B"), "final_score"] = 2.0

        result = run_single_backtest(panel, "final_score", self.config)

        replacement_nav = (1 - 0.0015) / (1 + 0.0015)
        self.assertAlmostEqual(result.returns.iloc[1], 1 / 1.0015 - 1)
        self.assertAlmostEqual(result.returns.iloc[2], replacement_nav - 1)
        swapped = result.trades.loc[result.trades["effective_date"].eq(self.dates[2])]
        np.testing.assert_allclose(swapped["weight_change"], [-1.0, replacement_nav])
        self.assertAlmostEqual(result.turnover.iloc[2], (1 + replacement_nav) / 2)
        np.testing.assert_allclose(swapped["weight_change"], swapped["weight_after"] - swapped["weight_before"])

    def test_frozen_partial_cash_target_charges_only_actual_asset_trade(self) -> None:
        panel = self.panel()

        # The existing selector is fully invested; this frozen target isolates
        # simulator accounting for an allowed partially invested weight vector.
        targets = []
        for i in range(len(self.dates)):
            selected = panel.xs(self.dates[i], level="date").copy()
            selected["target_weight"] = 1.0 if i == 0 else 0.5
            targets.append(selected)
        with patch("src.backtest.select_portfolio", side_effect=targets):
            result = run_single_backtest(panel, "final_score", self.config)
        partial_nav = (1 - 0.0015) / (1 - 0.0015 / 2)
        self.assertAlmostEqual(result.returns.iloc[2], partial_nav - 1)
        np.testing.assert_allclose(result.trades["weight_change"], [1 / 1.0015, partial_nav / 2 - 1])
        self.assertAlmostEqual(result.holdings.loc[result.holdings["date"].eq(self.dates[2]), "weight"].iloc[0], 0.5)

    def test_legal_all_cash_has_observed_zero_returns_not_empty_evidence(self) -> None:
        panel = self.panel()
        panel["in_universe"] = False
        panel["final_score"] = np.nan
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(len(result.returns), len(self.dates))
        self.assertTrue(result.returns.eq(0).all())
        self.assertTrue(result.equity_curve.eq(1).all())
        self.assertTrue(result.holdings.empty)
        self.assertTrue(result.trades.empty)

    def test_valid_frozen_empty_selection_produces_cash(self) -> None:
        config = {"strategy": {**self.config["strategy"], "top_n": 0}}
        result = run_single_backtest(self.panel(), "final_score", config)
        self.assertEqual(len(result.returns), len(self.dates))
        self.assertTrue(result.returns.eq(0).all())

    def test_nonfinite_eligible_scores_are_not_cash(self) -> None:
        for bad_score in (np.inf, -np.inf):
            with self.subTest(bad_score=bad_score):
                panel = self.panel(("A", "B"))
                panel.loc[(self.dates[1], "A"), "final_score"] = bad_score
                with self.assertRaisesRegex(ValueError, "score"):
                    run_single_backtest(panel, "final_score", self.config)

    def test_partial_missing_scores_keep_existing_candidate_exclusion(self) -> None:
        panel = self.panel(("A", "B"))
        panel.loc[(slice(None), "B"), "final_score"] = np.nan
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(result.trades["symbol"].tolist(), ["A"])

    def test_rule_scores_remain_valid_without_model_predictions(self) -> None:
        panel = self.panel().rename(columns={"final_score": "rule_score"})
        panel["prediction_window_count"] = 0
        result = run_single_backtest(panel, "rule_score", self.config)
        self.assertEqual(len(result.returns), len(self.dates))
        self.assertEqual(len(result.trades), 1)

    def test_bad_price_without_exposure_is_not_required(self) -> None:
        panel = self.panel(("A", "B"))
        panel.loc[(slice(None), "B"), "final_score"] = 0.5
        panel.loc[(slice(None), "B"), "open"] = np.nan
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(result.trades["symbol"].tolist(), ["A"])

    def test_bad_universe_or_required_open_is_not_cash_or_zero_return(self) -> None:
        for column, value in (("in_universe", None), ("in_universe", "false"),
                              ("open", np.nan), ("open", 0.0), ("open", -1.0), ("open", np.inf)):
            with self.subTest(column=column, value=value):
                panel = self.panel()
                if column == "in_universe":
                    panel[column] = panel[column].astype(object)
                panel.loc[(self.dates[2], "A"), column] = value
                with self.assertRaises(ValueError):
                    run_single_backtest(panel, "final_score", self.config)

    def test_explicit_leading_model_warmup_is_excluded_not_reported_as_cash(self) -> None:
        panel = self.panel()
        panel["prediction_window_count"] = 1
        panel.loc[(self.dates[:2], "A"), "final_score"] = np.nan
        panel.loc[(self.dates[:2], "A"), "prediction_window_count"] = 0
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(result.returns.index.tolist(), list(self.dates[3:]))
        self.assertEqual(result.trades.iloc[0]["signal_date"], self.dates[2])

    def test_missing_scores_after_start_or_without_warmup_marker_are_incomplete(self) -> None:
        for with_marker in (False, True):
            with self.subTest(with_marker=with_marker):
                panel = self.panel()
                panel.loc[(self.dates[2], "A"), "final_score"] = np.nan
                if with_marker:
                    panel["prediction_window_count"] = 1
                    panel.loc[(self.dates[2], "A"), "prediction_window_count"] = 0
                with self.assertRaisesRegex(ValueError, "score"):
                    run_single_backtest(panel, "final_score", self.config)

    def test_only_model_warmup_cannot_be_reported_as_all_cash(self) -> None:
        panel = self.panel()
        panel["prediction_window_count"] = 0
        panel["final_score"] = np.nan
        with self.assertRaisesRegex(ValueError, "incomplete"):
            run_single_backtest(panel, "final_score", self.config)

    def test_unexecutable_final_signal_does_not_require_a_score(self) -> None:
        panel = self.panel()
        expected = run_single_backtest(panel, "final_score", self.config)
        panel.loc[(self.dates[-1], "A"), "final_score"] = np.nan
        result = run_single_backtest(panel, "final_score", self.config)
        pd.testing.assert_series_equal(result.returns, expected.returns)
        pd.testing.assert_frame_equal(result.trades, expected.trades)

    def test_cash_prefix_does_not_turn_first_model_warmup_into_a_failed_window(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[0], "A"), "in_universe"] = False
        panel["prediction_window_count"] = 1
        panel.loc[(self.dates[:3], "A"), "prediction_window_count"] = 0
        panel.loc[(self.dates[:3], "A"), "final_score"] = np.nan
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(result.returns.index.tolist(), list(self.dates[4:]))
        self.assertEqual(result.trades["signal_date"].tolist(), [self.dates[3]])
        self.assertEqual(result.trades["effective_date"].tolist(), [self.dates[4]])
        self.assertAlmostEqual(result.returns.iloc[0], 1 / 1.0015 - 1)

    def test_cash_prefix_with_only_model_warmup_remains_incomplete(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[0], "A"), "in_universe"] = False
        panel["prediction_window_count"] = 0
        panel["final_score"] = np.nan
        with self.assertRaisesRegex(ValueError, "incomplete"):
            run_single_backtest(panel, "final_score", self.config)

    def test_available_oos_cash_decision_does_not_hide_a_later_failed_window(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[0], "A"), "in_universe"] = False
        panel["prediction_window_count"] = 1
        panel.loc[(self.dates[1], "A"), "prediction_window_count"] = 0
        panel.loc[(self.dates[1], "A"), "final_score"] = np.nan
        with self.assertRaisesRegex(ValueError, "score"):
            run_single_backtest(panel, "final_score", self.config)

    def test_non_rebalance_holdings_drift_without_free_target_reset(self) -> None:
        panel = self.panel(("A", "B"))
        panel.loc[(self.dates[2], "A"), "open"] = 200.0
        config = {"strategy": {**self.config["strategy"], "rebalance_frequency": "monthly",
                               "top_n": 2, "fee_bps": 0, "slippage_bps": 0}}
        result = run_single_backtest(panel, "final_score", config)
        self.assertAlmostEqual(result.equity_curve.iloc[-1], 1.0)
        self.assertEqual(len(result.trades), 2)
        self.assertEqual(result.trades["effective_date"].unique().tolist(), [self.dates[1]])
        peak_holdings = result.holdings.loc[result.holdings["date"].eq(self.dates[2])].set_index("symbol")
        self.assertAlmostEqual(peak_holdings.loc["A", "weight"], 2 / 3)
        self.assertAlmostEqual(peak_holdings.loc["B", "weight"], 1 / 3)

    def test_self_financed_entry_cost_compounds_with_following_price_move(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[2:], "A"), "open"] = 200.0
        config = {"strategy": {**self.config["strategy"], "rebalance_frequency": "monthly"}}
        result = run_single_backtest(panel, "final_score", config)
        # One cash unit buys 1 / (1 + 0.0015) notional: fees cannot be borrowed.
        # The purchased shares, not the pre-fee target, then double in value.
        expected_equity = 2 / 1.0015
        self.assertAlmostEqual(result.equity_curve.iloc[-1], expected_equity)
        self.assertAlmostEqual(result.returns.iloc[1], expected_equity - 1)

    def test_cost_rate_must_be_finite_nonnegative_and_below_one(self) -> None:
        for fee_bps in (-1, float("nan"), float("inf"), 10000, 10001):
            with self.subTest(fee_bps=fee_bps):
                config = {"strategy": {**self.config["strategy"], "fee_bps": fee_bps, "slippage_bps": 0}}
                with self.assertRaisesRegex(ValueError, "cost"):
                    run_single_backtest(self.panel(), "final_score", config)

    def test_self_financed_entry_does_not_borrow_cash_even_at_high_cost(self) -> None:
        for fee_bps in (0, 15, 9900):
            with self.subTest(fee_bps=fee_bps):
                config = {"strategy": {**self.config["strategy"], "fee_bps": fee_bps, "slippage_bps": 0}}
                result = run_single_backtest(self.panel(), "final_score", config)
                cost = fee_bps / 10000
                notional = result.trades.iloc[0]["weight_change"]
                self.assertAlmostEqual(notional * (1 + cost), 1.0, places=12)
                self.assertAlmostEqual(result.equity_curve.iloc[-1], 1 / (1 + cost), places=12)
                self.assertEqual(len(result.trades), 1)

    def test_held_next_open_is_required_even_when_the_next_decision_exits(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[1:], "A"), "in_universe"] = False
        panel.loc[(self.dates[2], "A"), "open"] = float("nan")
        with self.assertRaisesRegex(ValueError, "open"):
            run_single_backtest(panel, "final_score", self.config)

    def test_terminal_day_keeps_execution_cost_without_a_future_return(self) -> None:
        panel = self.panel()
        panel.loc[(self.dates[:4], "A"), "in_universe"] = False
        result = run_single_backtest(panel, "final_score", self.config)
        self.assertEqual(len(result.returns), len(self.dates))
        self.assertAlmostEqual(result.returns.iloc[-1], 1 / 1.0015 - 1)
        self.assertEqual(result.trades["effective_date"].tolist(), [self.dates[-1]])

    def test_actual_cash_deficit_is_not_clipped_as_roundoff(self) -> None:
        panel = self.panel()
        selected = panel.xs(self.dates[0], level="date").copy()
        selected["target_weight"] = 1 + 1e-13
        with patch("src.backtest.select_portfolio", return_value=selected):
            with self.assertRaisesRegex(ValueError, "cash"):
                run_single_backtest(panel, "final_score", self.config)


if __name__ == "__main__":
    unittest.main()
