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
    if "in_universe" not in panel or not panel["in_universe"].map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise ValueError("Backtest requires complete boolean universe membership.")
    if score_column not in panel:
        raise ValueError("Backtest requires the requested score column.")
    dates = list(panel.index.get_level_values("date").unique().sort_values())
    rebalance_dates = make_schedule(dates, strategy_cfg["rebalance_frequency"])
    all_symbols = sorted(panel.loc[panel["in_universe"]].index.get_level_values("symbol").unique())

    events: dict[pd.Timestamp, tuple[pd.Timestamp, pd.DataFrame]] = {}
    skipped_warmup = False
    scoring_start_date: pd.Timestamp | None = None
    for signal_date in rebalance_dates:
        effective_date = next_trading_date(dates, signal_date, int(strategy_cfg["signal_lag_days"]))
        if effective_date is None:
            continue
        snapshot = panel.xs(signal_date, level="date")
        eligible = snapshot.loc[snapshot["in_universe"]]
        scores = eligible[score_column]
        if not eligible.empty and scores.isna().all():
            if (
                scoring_start_date is None
                and score_column in {"linear_score", "ml_score", "final_score"}
                and "prediction_window_count" in eligible
                and eligible["prediction_window_count"].eq(0).all()
            ):
                skipped_warmup = True
                continue
            raise ValueError("Backtest has incomplete eligible scores.")
        if not np.isfinite(scores.dropna()).all():
            raise ValueError("Backtest requires finite eligible scores.")
        if scoring_start_date is None and (
            scores.notna().any()
            or ("prediction_window_count" in snapshot and snapshot["prediction_window_count"].gt(0).any())
        ):
            scoring_start_date = effective_date
        selected = select_portfolio(
            snapshot=snapshot,
            score_column=score_column,
            top_n=int(strategy_cfg["top_n"]),
            weighting=strategy_cfg["weighting"],
        )
        if not selected.empty:
            weights = selected["target_weight"]
            if not np.isfinite(weights).all() or weights.lt(0).any() or weights.sum() > 1.0 + 1e-12:
                raise ValueError("Backtest requires finite long-only target weights without leverage.")
        events[effective_date] = (signal_date, selected)

    if skipped_warmup and scoring_start_date is None:
        raise ValueError("Backtest is incomplete: no post-warmup executable decision.")
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

    trading_dates = pd.DatetimeIndex(dates)
    if skipped_warmup:
        # Exclude the entire pre-OOS prefix, including legal cash decisions,
        # instead of counting unavailable model research periods as cash returns.
        trading_dates = trading_dates[trading_dates >= scoring_start_date]
    open_matrix = wide_field_from_panel(panel, "open").reindex(index=trading_dates, columns=all_symbols)
    open_matrix = open_matrix.apply(pd.to_numeric, errors="coerce")
    execution_cost = float(strategy_cfg["fee_bps"] + strategy_cfg["slippage_bps"]) / 10000.0
    if not np.isfinite(execution_cost) or not 0 <= execution_cost < 1:
        raise ValueError("Backtest execution cost must be finite, non-negative and below one.")

    weight_matrix = pd.DataFrame(0.0, index=trading_dates, columns=all_symbols)
    turnover_series = pd.Series(0.0, index=trading_dates, dtype=float)
    net_returns = pd.Series(0.0, index=trading_dates, dtype=float)
    trade_rows = []
    shares = pd.Series(0.0, index=weight_matrix.columns, dtype=float)
    cash = 1.0

    for position, date in enumerate(trading_dates):
        prices = open_matrix.loc[date]
        held = shares.ne(0)
        if not (np.isfinite(prices.loc[held]) & prices.loc[held].gt(0)).all():
            raise ValueError("Backtest requires finite positive open prices for holdings and trades.")
        asset_values = (shares * prices).where(held, 0.0)
        pretrade_nav = float(cash + asset_values.sum())
        if not np.isfinite(pretrade_nav) or pretrade_nav <= 0:
            raise ValueError("Backtest requires finite positive portfolio equity.")
        if date in events:
            signal_date, event_frame = events[date]
            next_weights = build_weight_vector(event_frame, weight_matrix.columns)
            selected = next_weights.ne(0)
            if not (np.isfinite(prices.loc[selected]) & prices.loc[selected].gt(0)).all():
                raise ValueError("Backtest requires finite positive open prices for holdings and trades.")

            # Solve postfee NAV + per-side trading costs = pretrade NAV.
            # The long-only target and 0 <= cost < 1 give a unique scalar root.
            target = next_weights.to_numpy()
            before = asset_values.to_numpy()
            lower, upper = 0.0, pretrade_nav
            if execution_cost and np.abs(target * pretrade_nav - before).sum():
                for _ in range(64):
                    midpoint = (lower + upper) / 2
                    required = midpoint + execution_cost * np.abs(target * midpoint - before).sum()
                    if required > pretrade_nav:
                        upper = midpoint
                    else:
                        lower = midpoint
                postfee_nav = lower
            else:
                postfee_nav = pretrade_nav
            target_values = next_weights * postfee_nav
            trade_values = target_values - asset_values
            fees = execution_cost * float(trade_values.abs().sum())
            next_cash = float(cash - trade_values.sum() - fees)
            cash_tolerance = np.finfo(float).eps * pretrade_nav * max(16, len(shares))
            if not np.isfinite(next_cash) or next_cash < -cash_tolerance:
                raise ValueError("Backtest rebalance cannot overdraw cash.")
            cash = max(0.0, next_cash)  # Only floating-point-sized deficits reach here.
            before_weights = asset_values / pretrade_nav
            after_weights = target_values / pretrade_nav
            turnover_series.loc[date] = calculate_turnover(before_weights, after_weights)
            changed = trade_values / pretrade_nav
            for symbol, change in changed[changed.round(12) != 0.0].items():
                trade_rows.append(
                    {
                        "effective_date": date,
                        "signal_date": signal_date,
                        "symbol": symbol,
                        "weight_before": float(before_weights.loc[symbol]),
                        "weight_after": float(after_weights.loc[symbol]),
                        "weight_change": float(change),
                        "score_source": score_column,
                    }
                )
            shares = (target_values / prices).where(selected, 0.0)
            asset_values = target_values
        posttrade_nav = float(cash + asset_values.sum())
        weight_matrix.loc[date] = asset_values / posttrade_nav
        next_prices = open_matrix.iloc[position + 1] if position + 1 < len(trading_dates) else prices
        held = shares.ne(0)
        if not (np.isfinite(next_prices.loc[held]) & next_prices.loc[held].gt(0)).all():
            raise ValueError("Backtest requires finite positive next open prices for held assets.")
        next_nav = float(cash + (shares * next_prices).where(held, 0.0).sum())
        if not np.isfinite(next_nav) or next_nav <= 0:
            raise ValueError("Backtest requires finite positive portfolio equity.")
        net_returns.loc[date] = next_nav / pretrade_nav - 1.0
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
