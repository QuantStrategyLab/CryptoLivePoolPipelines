from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.backtest import (
    aggregate_walkforward_predictions,
    build_walkforward_windows,
    resolve_walkforward_purge_days,
    run_walkforward_scoring,
)
from src.evaluation import evaluate_live_pool_shadow, summarize_live_pool_shadow
from src.labels import build_labels
from src.models import ModelPredictionResult


class WalkforwardValidationTests(unittest.TestCase):
    def test_build_walkforward_windows_defaults_purge_to_max_label_horizon(self) -> None:
        dates = list(pd.date_range("2024-01-01", periods=8, freq="D"))
        config = {
            "walkforward": {
                "train_window_days": 4,
                "test_window_days": 2,
                "step_days": 2,
                "purge_days": None,
            },
            "labels": {"horizons": [1, 2]},
        }

        windows = build_walkforward_windows(dates, config)

        self.assertEqual(windows[0]["purge_days"], 2)
        self.assertEqual(windows[0]["train_end"], pd.Timestamp("2024-01-04"))
        self.assertEqual(windows[0]["effective_train_end"], pd.Timestamp("2024-01-02"))

    def test_run_walkforward_scoring_uses_effective_train_end(self) -> None:
        dates = pd.date_range("2024-01-01", periods=8, freq="D")
        symbols = ["AAA", "BBB"]
        index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
        panel = pd.DataFrame(index=index)
        panel["in_universe"] = True
        panel["blended_target"] = 1.0
        panel["feature_a"] = 1.0

        config = {
            "walkforward": {
                "train_window_days": 4,
                "test_window_days": 2,
                "step_days": 2,
                "purge_days": 2,
                "prediction_aggregation": "mean",
            },
            "labels": {"horizons": [1, 2]},
            "model": {"min_train_rows": 1},
        }

        captured_train_max_dates: list[pd.Timestamp] = []

        def fake_fit_predict_models(
            train_df: pd.DataFrame,
            score_df: pd.DataFrame,
            feature_columns: list[str],
            config: dict[str, object],
        ) -> ModelPredictionResult:
            captured_train_max_dates.append(train_df.index.get_level_values("date").max())
            predictions = pd.DataFrame(index=score_df.index)
            predictions["linear_score_raw"] = np.arange(len(score_df), dtype=float)
            predictions["ml_score_raw"] = np.arange(len(score_df), dtype=float)
            return ModelPredictionResult(
                predictions=predictions,
                linear_backend="fake_linear",
                ml_backend="fake_ml",
                train_rows=len(train_df),
                test_rows=len(score_df),
            )

        with patch("src.backtest.fit_predict_models", fake_fit_predict_models):
            scored, window_summary = run_walkforward_scoring(panel, ["feature_a"], config)

        self.assertEqual(captured_train_max_dates[0], pd.Timestamp("2024-01-02"))
        self.assertEqual(int(window_summary.iloc[0]["train_rows_pre_purge"]), 8)
        self.assertEqual(int(window_summary.iloc[0]["purged_train_rows"]), 4)
        self.assertEqual(int(scored["prediction_window_count"].max()), 1)

    def test_purge_rejects_short_or_invalid_override_without_coercing_it(self) -> None:
        for purge in (0, 1, -1, 2.5, True, False, "bad", "2.5", float("nan"), float("inf"), [], {}):
            with self.subTest(purge=purge):
                config = {"walkforward": {"purge_days": purge}, "labels": {"horizons": [1, 2]}}
                with self.assertRaises(ValueError):
                    resolve_walkforward_purge_days(config)
                self.assertIs(config["walkforward"]["purge_days"], purge)

    def test_purge_keeps_default_equal_and_larger_integer_values(self) -> None:
        for purge, expected in ((None, 2), (2, 2), (3, 3), ("2", 2), (2.0, 2)):
            with self.subTest(purge=purge):
                config = {"walkforward": {"purge_days": purge}, "labels": {"horizons": [1, 2]}}
                self.assertEqual(resolve_walkforward_purge_days(config), expected)
                self.assertIs(config["walkforward"]["purge_days"], purge)
        self.assertEqual(resolve_walkforward_purge_days({}), 0)

    def test_purge_cannot_clamp_an_empty_training_window_to_one_row(self) -> None:
        dates = list(pd.date_range("2024-01-01", periods=8, freq="D"))
        for purge in (4, 5, 20):
            with self.subTest(purge=purge):
                config = {
                    "walkforward": {"train_window_days": 4, "test_window_days": 2,
                                    "step_days": 2, "purge_days": purge},
                    "labels": {"horizons": [2]},
                }
                with self.assertRaisesRegex(ValueError, "training"):
                    build_walkforward_windows(dates, config)

    def test_scoring_rejects_short_purge_before_fitting(self) -> None:
        index = pd.MultiIndex.from_product(
            [pd.date_range("2024-01-01", periods=8), ["AAA"]], names=["date", "symbol"]
        )
        panel = pd.DataFrame({"in_universe": True, "blended_target": 1.0}, index=index)
        config = {
            "walkforward": {"train_window_days": 4, "test_window_days": 2,
                            "step_days": 2, "purge_days": 1},
            "labels": {"horizons": [1, 2]},
        }
        with patch("src.backtest.fit_predict_models") as fit:
            with self.assertRaises(ValueError):
                run_walkforward_scoring(panel, [], config)
            fit.assert_not_called()

    def test_sparse_cross_section_training_labels_end_before_every_test(self) -> None:
        dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-05",
                                "2024-01-08", "2024-01-09", "2024-01-12", "2024-01-13",
                                "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-20"])
        index = pd.MultiIndex.from_tuples(
            [(date, "AAA") for date in dates]
            + [(dates[i], "BBB") for i in (0, 1, 4, 6, 8, 10)], names=["date", "symbol"]
        )
        raw = pd.DataFrame({"close": 10.0, "in_universe": True, "feature_a": 1.0}, index=index)
        config = {
            "walkforward": {"train_window_days": 6, "test_window_days": 2,
                            "step_days": 2, "purge_days": 2},
            "labels": {"horizons": [1, 2], "future_top_k": 1,
                       "target_mode": "blended_rank_pct", "blended_rank_weights": {1: 0.5, 2: 0.5}},
        }
        captured: list[pd.DataFrame] = []

        def capture(train_df, score_df, feature_columns, config):
            captured.append(train_df.copy())
            return ModelPredictionResult(
                predictions=pd.DataFrame(index=score_df.index), linear_backend="fake",
                ml_backend="fake", train_rows=len(train_df), test_rows=len(score_df),
            )

        labelled = build_labels(raw, config)
        with patch("src.backtest.fit_predict_models", capture):
            _, summary = run_walkforward_scoring(labelled, ["feature_a"], config)

        self.assertEqual(len(captured), 3)
        self.assertTrue(all(not frame.empty for frame in captured))
        for train, window in zip(captured, summary.to_dict("records")):
            for date in train.index.get_level_values("date").unique():
                for symbol in ("AAA", "BBB"):
                    symbol_dates = labelled.xs(symbol, level="symbol").index
                    if date not in symbol_dates:
                        continue
                    position = symbol_dates.get_loc(date)
                    for horizon in config["labels"]["horizons"]:
                        if position + horizon < len(symbol_dates):
                            self.assertLess(symbol_dates[position + horizon], window["test_start"])
        # BBB's label on Jan 2 reaches the first test on Jan 12. Its rank also
        # contaminates AAA on Jan 2, although AAA's own labels end in training.
        self.assertNotIn((dates[1], "AAA"), captured[0].index)
        before = captured[0]
        raw.loc[(raw.index.get_level_values("date") >= dates[6])
                & (raw.index.get_level_values("symbol") == "BBB"), "close"] = 1000.0
        changed = build_labels(raw, config)
        self.assertNotEqual(labelled.loc[(dates[1], "AAA"), "blended_target"],
                            changed.loc[(dates[1], "AAA"), "blended_target"])
        captured.clear()
        with patch("src.backtest.fit_predict_models", capture):
            run_walkforward_scoring(changed, ["feature_a"], config)
        pd.testing.assert_frame_equal(before, captured[0])

    def test_aggregate_walkforward_predictions_supports_latest_mode(self) -> None:
        index = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2024-02-01"), "AAA"),
                (pd.Timestamp("2024-02-01"), "AAA"),
                (pd.Timestamp("2024-02-01"), "BBB"),
            ],
            names=["date", "symbol"],
        )
        prediction_frame = pd.DataFrame(
            {
                "linear_score_raw": [1.0, 3.0, 2.0],
                "ml_score_raw": [5.0, 7.0, 4.0],
                "window_id": [0, 1, 1],
            },
            index=index,
        )

        mean_aggregated = aggregate_walkforward_predictions(prediction_frame, aggregation_mode="mean")
        latest_aggregated = aggregate_walkforward_predictions(prediction_frame, aggregation_mode="latest")

        mean_row = mean_aggregated.loc[(pd.Timestamp("2024-02-01"), "AAA")]
        latest_row = latest_aggregated.loc[(pd.Timestamp("2024-02-01"), "AAA")]

        self.assertEqual(mean_row["linear_score_raw"], 2.0)
        self.assertEqual(mean_row["prediction_window_count"], 2)
        self.assertEqual(latest_row["linear_score_raw"], 3.0)
        self.assertEqual(latest_row["prediction_window_count"], 2)
        self.assertEqual(latest_row["prediction_source_window_id"], 1)

    def test_evaluate_live_pool_shadow_and_summary(self) -> None:
        dates = [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")]
        symbols = ["AAA", "BBB", "CCC"]
        index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
        panel = pd.DataFrame(index=index)
        panel["in_universe"] = True
        panel["final_score"] = [
            0.9, 0.8, 0.1,
            0.2, 0.95, 0.85,
        ]
        panel["future_return_30"] = [
            0.4, 0.2, 0.1,
            0.0, 0.1, 0.5,
        ]

        config = {
            "export": {"live_pool_size": 2},
            "labels": {"horizons": [30], "future_top_k": 1},
        }

        shadow = evaluate_live_pool_shadow(
            panel,
            score_column="final_score",
            config=config,
            rebalance_frequency="monthly",
            pool_size=2,
        )
        summary = summarize_live_pool_shadow(shadow)

        self.assertEqual(len(shadow), 2)
        self.assertEqual(shadow.iloc[0]["pool_symbols"], "AAA,BBB")
        self.assertEqual(shadow.iloc[1]["pool_symbols"], "BBB,CCC")
        self.assertEqual(shadow.iloc[1]["pool_churn"], 0.5)
        self.assertEqual(shadow.iloc[0]["h30_precision"], 0.5)
        self.assertEqual(shadow.iloc[1]["h30_leader_capture"], 1.0)
        self.assertEqual(int(summary.iloc[0]["evaluation_dates"]), 2)
        self.assertEqual(summary.iloc[0]["pool_churn"], 0.5)
        self.assertEqual(summary.iloc[0]["h30_precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
