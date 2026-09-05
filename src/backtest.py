from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .evaluation import compute_performance_metrics
from .models import fit_predict_models
from .portfolio import build_weight_vector, calculate_turnover, select_portfolio
from .utils import make_schedule, next_trading_date, wide_field_from_panel


@dataclass
class BacktestResult:
    name: str
    returns: pd.Series
    equity_curve: pd.Series
    holdings: pd.DataFrame
    trades: pd.DataFrame
    turnover: pd.Series
    metrics: dict[str, float]


def resolve_walkforward_purge_days(config: dict[str, Any]) -> int:
    """Resolve the walk-forward label purge/embargo in trading days."""
    walk_cfg = config.get("walkforward", {})
    configured = walk_cfg.get("purge_days")
    horizons = [int(horizon) for horizon in config.get("labels", {}).get("horizons", [])]
    minimum_purge = max(horizons, default=0)
    if configured is None:
        return minimum_purge
    try:
        purge_days = int(configured)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("walkforward.purge_days must be a non-negative integer.") from None
    if isinstance(configured, bool) or (not isinstance(configured, str) and purge_days != configured):
        raise ValueError("walkforward.purge_days must be a non-negative integer.")
    if purge_days < max(0, minimum_purge):
        raise ValueError("walkforward.purge_days must cover every label horizon and be non-negative.")
    return purge_days


def build_walkforward_windows(dates: list[pd.Timestamp], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Create rolling train/test windows over a daily crypto calendar."""
    walk_cfg = config["walkforward"]
    train_window = int(walk_cfg["train_window_days"])
    test_window = int(walk_cfg["test_window_days"])
    step_days = int(walk_cfg["step_days"])
    purge_days = resolve_walkforward_purge_days(config)
    if purge_days >= train_window:
        raise ValueError("walkforward.purge_days must leave at least one training date.")

    ordered_dates = list(pd.DatetimeIndex(dates).sort_values().unique())
    if len(ordered_dates) <= train_window:
        return []

    windows = []
    cursor = train_window
    window_id = 0
    while cursor < len(ordered_dates):
        train_start_position = max(0, cursor - train_window)
        train_end_position = cursor - 1
        effective_train_end_position = train_end_position - purge_days
        train_start = ordered_dates[train_start_position]
        train_end = ordered_dates[train_end_position]
        effective_train_end = ordered_dates[effective_train_end_position]
        test_start = ordered_dates[cursor]
        test_end_position = min(len(ordered_dates) - 1, cursor + test_window - 1)
        test_end = ordered_dates[test_end_position]
        windows.append(
            {
                "window_id": window_id,
                "train_start": train_start,
                "train_end": train_end,
                "effective_train_end": effective_train_end,
                "test_start": test_start,
                "test_end": test_end,
                "purge_days": purge_days,
            }
        )
        if test_end_position >= len(ordered_dates) - 1:
            break
        cursor += step_days
        window_id += 1
    return windows


def aggregate_walkforward_predictions(
    prediction_frame: pd.DataFrame,
    aggregation_mode: str = "mean",
) -> pd.DataFrame:
    """Aggregate duplicate OOS predictions created by overlapping test windows."""
    if prediction_frame.empty:
        return prediction_frame

    aggregation_mode = str(aggregation_mode).lower()
    flat = prediction_frame.reset_index().sort_values(["date", "symbol", "window_id"])
    counts = (
        flat.groupby(["date", "symbol"], as_index=False)["window_id"]
        .nunique()
        .rename(columns={"window_id": "prediction_window_count"})
    )

    if aggregation_mode == "mean":
        aggregated = (
            flat.groupby(["date", "symbol"], as_index=False)[["linear_score_raw", "ml_score_raw"]]
            .mean()
        )
    elif aggregation_mode == "latest":
        aggregated = flat.groupby(["date", "symbol"], as_index=False).tail(1)[
            ["date", "symbol", "linear_score_raw", "ml_score_raw", "window_id"]
        ].rename(columns={"window_id": "prediction_source_window_id"})
    else:
        raise ValueError(f"Unsupported walk-forward prediction aggregation mode: {aggregation_mode}")

    aggregated = aggregated.merge(counts, on=["date", "symbol"], how="left")
    return aggregated.set_index(["date", "symbol"]).sort_index()


def run_walkforward_scoring(
    panel: pd.DataFrame,
    feature_columns: list[str],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train rolling models and attach out-of-sample predictions to the panel."""
    panel = panel.copy()
    dates = list(panel.index.get_level_values("date").unique().sort_values())
    windows = build_walkforward_windows(dates, config)
    aggregation_mode = str(config.get("walkforward", {}).get("prediction_aggregation", "mean")).lower()
    label_dates = pd.Series(panel.index.get_level_values("date"), index=panel.index).sort_index()
    horizons = [int(horizon) for horizon in config.get("labels", {}).get("horizons", [])]
    label_ends = pd.concat(
        [label_dates.groupby(level="symbol").shift(-horizon) for horizon in horizons] or [label_dates],
        axis=1,
    ).max(axis=1).reindex(panel.index)
    # Labels shift within each symbol, not the union calendar. Cross-sectional
    # ranks also depend on the latest outcome of every eligible peer that day.
    label_ends = label_ends.where(panel["in_universe"]).groupby(level="date").transform("max")

    all_predictions = []
    window_rows = []
    for window in windows:
        date_index = panel.index.get_level_values("date")
        pre_purge_train_mask = (
            (date_index >= window["train_start"])
            & (date_index <= window["train_end"])
            & panel["in_universe"]
            & panel["blended_target"].notna()
        )
        train_mask = (
            pre_purge_train_mask
            & (date_index <= window["effective_train_end"])
            & (label_ends < window["test_start"])
        )
        test_mask = (
            (date_index >= window["test_start"])
            & (date_index <= window["test_end"])
            & panel["in_universe"]
        )
        train_df = panel.loc[train_mask].copy()
        test_df = panel.loc[test_mask].copy()
        result = fit_predict_models(train_df, test_df, feature_columns, config)
        train_dates_pre_purge = int(
            panel.loc[pre_purge_train_mask].index.get_level_values("date").nunique()
        )
        train_dates = int(train_df.index.get_level_values("date").nunique())

        if not result.predictions.empty:
            current_predictions = result.predictions.copy()
            current_predictions["window_id"] = window["window_id"]
            all_predictions.append(current_predictions)

        window_rows.append(
            {
                **window,
                "prediction_aggregation": aggregation_mode,
                "train_rows_pre_purge": int(pre_purge_train_mask.sum()),
                "purged_train_rows": int(pre_purge_train_mask.sum() - train_mask.sum()),
                "train_rows": result.train_rows,
                "train_dates_pre_purge": train_dates_pre_purge,
                "train_dates": train_dates,
                "test_rows": result.test_rows,
                "linear_backend": result.linear_backend,
                "ml_backend": result.ml_backend,
            }
        )

    if all_predictions:
        prediction_frame = pd.concat(all_predictions).sort_index()
        aggregated = aggregate_walkforward_predictions(prediction_frame, aggregation_mode=aggregation_mode)
        panel = panel.join(aggregated, how="left")
    else:
        panel["linear_score_raw"] = np.nan
        panel["ml_score_raw"] = np.nan
        panel["prediction_window_count"] = 0

    window_summary = pd.DataFrame(window_rows)
    return panel, window_summary


def run_single_backtest(
    panel: pd.DataFrame,
    score_column: str,
    config: dict[str, Any],
) -> BacktestResult:
    """Run a long-only daily open-to-open backtest from cross-sectional scores."""
    strategy_cfg = config["strategy"]
    dates = list(panel.index.get_level_values("date").unique().sort_values())
    rebalance_dates = make_schedule(dates, strategy_cfg["rebalance_frequency"])
    all_symbols = sorted(panel.loc[panel["in_universe"]].index.get_level_values("symbol").unique())

    events: list[dict[str, Any]] = []
    for signal_date in rebalance_dates:
        snapshot = panel.xs(signal_date, level="date")
        selected = select_portfolio(
            snapshot=snapshot,
            score_column=score_column,
            top_n=int(strategy_cfg["top_n"]),
            weighting=strategy_cfg["weighting"],
        )
        if selected.empty:
            continue
        effective_date = next_trading_date(dates, signal_date, int(strategy_cfg["signal_lag_days"]))
        if effective_date is None:
            continue
        selected = selected.copy()
        selected["signal_date"] = signal_date
        selected["effective_date"] = effective_date
        selected["score_source"] = score_column
        selected = selected.reset_index().rename(columns={"index": "symbol"})
        events.extend(selected.to_dict("records"))

    if not events:
        empty_series = pd.Series(dtype=float)
        return BacktestResult(
            name=score_column,
            returns=empty_series,
            equity_curve=empty_series,
            holdings=pd.DataFrame(),
            trades=pd.DataFrame(),
            turnover=empty_series,
            metrics=compute_performance_metrics(empty_series),
        )

    events_df = pd.DataFrame(events).sort_values(["effective_date", "target_weight"], ascending=[True, False])
    trading_dates = pd.DatetimeIndex(dates)
    open_matrix = wide_field_from_panel(panel, "open").reindex(index=trading_dates, columns=all_symbols)
    open_to_open_returns = open_matrix.shift(-1).div(open_matrix).sub(1.0).fillna(0.0)

    weight_matrix = pd.DataFrame(0.0, index=trading_dates, columns=all_symbols)
    turnover_series = pd.Series(0.0, index=trading_dates, dtype=float)
    trade_rows = []
    current_weights = pd.Series(0.0, index=weight_matrix.columns, dtype=float)

    event_groups = {date: frame for date, frame in events_df.groupby("effective_date")}
    for date in trading_dates:
        if date in event_groups:
            event_frame = event_groups[date].set_index("symbol")
            next_weights = build_weight_vector(event_frame, weight_matrix.columns)
            turnover_value = calculate_turnover(current_weights, next_weights)
            turnover_series.loc[date] = turnover_value
            changed = (next_weights - current_weights).round(12)
            for symbol, change in changed[changed != 0.0].items():
                trade_rows.append(
                    {
                        "effective_date": date,
                        "signal_date": event_frame.iloc[0]["signal_date"],
                        "symbol": symbol,
                        "weight_before": float(current_weights.loc[symbol]),
                        "weight_after": float(next_weights.loc[symbol]),
                        "weight_change": float(change),
                        "score_source": score_column,
                    }
                )
            current_weights = next_weights
        weight_matrix.loc[date] = current_weights

    gross_returns = (weight_matrix * open_to_open_returns).sum(axis=1)
    execution_cost = float(strategy_cfg["fee_bps"] + strategy_cfg["slippage_bps"]) / 10000.0
    net_returns = gross_returns - turnover_series * execution_cost
    equity_curve = (1.0 + net_returns).cumprod()
    holdings = (
        weight_matrix.stack()
        .reset_index()
        .rename(columns={"level_0": "date", "level_1": "symbol", 0: "weight"})
        .loc[lambda df: df["weight"] != 0.0]
        .reset_index(drop=True)
    )
    trades = pd.DataFrame(trade_rows)
    metrics = compute_performance_metrics(net_returns, turnover_series)

    return BacktestResult(
        name=score_column,
        returns=net_returns,
        equity_curve=equity_curve,
        holdings=holdings,
        trades=trades,
        turnover=turnover_series,
        metrics=metrics,
    )


def run_backtest_suite(panel: pd.DataFrame, config: dict[str, Any]) -> dict[str, BacktestResult]:
    """Backtest the rule, linear, ML, and ensemble scores with the same engine."""
    score_columns = ["rule_score", "linear_score", "ml_score", "final_score"]
    results: dict[str, BacktestResult] = {}
    for score_column in score_columns:
        if score_column not in panel.columns:
            continue
        results[score_column] = run_single_backtest(panel, score_column, config)
    return results
