from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from .utils import make_schedule


def compute_performance_metrics(
    returns: pd.Series,
    turnover: Optional[pd.Series] = None,
    periods_per_year: int = 365,
) -> dict[str, float]:
    """Compute daily strategy performance metrics from a return series."""
    returns = returns.dropna()
    if returns.empty:
        return {
            "CAGR": np.nan,
            "Annualized Volatility": np.nan,
            "Sharpe": np.nan,
            "Sortino": np.nan,
            "Max Drawdown": np.nan,
            "Calmar": np.nan,
            "Win Rate": np.nan,
            "Turnover": np.nan,
        }

    equity = (1.0 + returns).cumprod()
    total_days = len(returns)
    cagr = float(equity.iloc[-1] ** (periods_per_year / total_days) - 1.0)
    ann_vol = float(returns.std(ddof=0) * np.sqrt(periods_per_year))
    ann_return = float(returns.mean() * periods_per_year)
    downside = returns.where(returns < 0.0, 0.0)
    downside_vol = float(np.sqrt((downside ** 2).mean()) * np.sqrt(periods_per_year))
    sharpe = ann_return / ann_vol if ann_vol > 0.0 else np.nan
    sortino = ann_return / downside_vol if downside_vol > 0.0 else np.nan
    drawdown = equity / equity.cummax().clip(lower=1.0) - 1.0
    max_drawdown = float(drawdown.min())
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0.0 else np.nan
    win_rate = float((returns > 0.0).mean())
    turnover_value = np.nan if turnover is None else float(turnover.mean() * periods_per_year)
    return {
        "CAGR": cagr,
        "Annualized Volatility": ann_vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Max Drawdown": max_drawdown,
        "Calmar": calmar,
        "Win Rate": win_rate,
        "Turnover": turnover_value,
    }


def evaluate_leader_selection(
    panel: pd.DataFrame,
    score_column: str,
    config: dict[str, Any],
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
    rebalance_dates: Optional[Iterable[pd.Timestamp]] = None,
) -> dict[str, dict[str, float]]:
    """Measure how well current rankings capture future leaders."""
    subset = panel.copy()
    date_index = subset.index.get_level_values("date")
    if start_date is not None:
        subset = subset.loc[date_index >= pd.Timestamp(start_date)]
        date_index = subset.index.get_level_values("date")
    if end_date is not None:
        subset = subset.loc[date_index <= pd.Timestamp(end_date)]
        date_index = subset.index.get_level_values("date")

    if rebalance_dates is None:
        rebalance_dates = make_schedule(
            list(subset.index.get_level_values("date").unique().sort_values()),
            config["strategy"]["rebalance_frequency"],
        )

    top_n = int(config["strategy"]["top_n"])
    future_top_k = int(config["labels"]["future_top_k"])
    horizons = [int(h) for h in config["labels"]["horizons"]]
    results: dict[str, dict[str, float]] = {}

    for horizon in horizons:
        precision_values: list[float] = []
        recall_values: list[float] = []
        hit_values: list[float] = []
        leader_capture_values: list[float] = []
        avg_rank_values: list[float] = []
        overlap_counts: list[int] = []

        future_return_col = f"future_return_{horizon}"
        for date in rebalance_dates:
            if date not in subset.index.get_level_values("date"):
                continue
            snapshot = subset.xs(date, level="date")
            valid = snapshot["in_universe"] & snapshot[score_column].notna() & snapshot[future_return_col].notna()
            current = snapshot.loc[valid].copy()
            if len(current) < max(top_n, future_top_k):
                continue

            current_top = current.sort_values(score_column, ascending=False).head(top_n).index.tolist()
            future_top = current.sort_values(future_return_col, ascending=False).head(future_top_k).index.tolist()
            overlap = sorted(set(current_top) & set(future_top))
            ranks = current[score_column].rank(ascending=False, method="first")

            precision_values.append(len(overlap) / top_n)
            recall_values.append(len(overlap) / future_top_k)
            hit_values.append(float(len(overlap) > 0))
            leader_capture_values.append(float(future_top[0] in current_top))
            avg_rank_values.append(float(ranks.loc[future_top].mean()))
            overlap_counts.append(len(overlap))

        results[str(horizon)] = {
            "Precision@N": float(np.mean(precision_values)) if precision_values else np.nan,
            "Recall@N": float(np.mean(recall_values)) if recall_values else np.nan,
            "Overlap Hit Rate": float(np.mean(hit_values)) if hit_values else np.nan,
            "Average Rank of Future Top Performers": float(np.mean(avg_rank_values)) if avg_rank_values else np.nan,
            "Leader Capture Rate": float(np.mean(leader_capture_values)) if leader_capture_values else np.nan,
            "Average Overlap Count": float(np.mean(overlap_counts)) if overlap_counts else np.nan,
            "Evaluation Dates": float(len(precision_values)),
        }

    return results


def leader_metrics_to_frame(metrics: Mapping[str, Mapping[str, float]]) -> pd.DataFrame:
    """Convert nested leader metrics into a tidy dataframe."""
    rows = []
    for horizon, horizon_metrics in metrics.items():
        row = {"horizon": horizon}
        row.update(horizon_metrics)
        rows.append(row)
    return pd.DataFrame(rows)


def evaluate_live_pool_shadow(
    panel: pd.DataFrame,
    score_column: str,
    config: dict[str, Any],
    rebalance_frequency: str = "monthly",
    pool_size: Optional[int] = None,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """Evaluate a fixed-size live-style pool on a slower rebalance cadence."""
    subset = panel.copy()
    date_index = subset.index.get_level_values("date")
    if start_date is not None:
        subset = subset.loc[date_index >= pd.Timestamp(start_date)]
        date_index = subset.index.get_level_values("date")
    if end_date is not None:
        subset = subset.loc[date_index <= pd.Timestamp(end_date)]
        date_index = subset.index.get_level_values("date")

    if pool_size is None:
        pool_size = int(config["export"]["live_pool_size"])
    future_top_k = int(config["labels"]["future_top_k"])
    horizons = [int(h) for h in config["labels"]["horizons"]]
    rebalance_dates = make_schedule(list(date_index.unique().sort_values()), rebalance_frequency)

    rows: list[dict[str, Any]] = []
    previous_pool: list[str] | None = None
    for rebalance_date in rebalance_dates:
        if rebalance_date not in subset.index.get_level_values("date"):
            continue
        snapshot = subset.xs(rebalance_date, level="date")
        eligible = snapshot.loc[snapshot["in_universe"] & snapshot[score_column].notna()].copy()
        if len(eligible) < pool_size:
            continue

        pool = eligible.sort_values(score_column, ascending=False).head(pool_size)
        pool_symbols = pool.index.tolist()
        pool_set = set(pool_symbols)
        overlap = len(pool_set & set(previous_pool or []))
        stability = np.nan if previous_pool is None else overlap / pool_size
        churn = np.nan if previous_pool is None else 1.0 - stability

        row: dict[str, Any] = {
            "rebalance_date": rebalance_date,
            "rebalance_frequency": rebalance_frequency,
            "pool_size": pool_size,
            "pool_symbols": ",".join(pool_symbols),
            "pool_stability": stability,
            "pool_churn": churn,
        }

        for horizon in horizons:
            future_return_col = f"future_return_{horizon}"
            horizon_slice = eligible.loc[eligible[future_return_col].notna()].copy()
            selected_symbols = [symbol for symbol in pool_symbols if symbol in horizon_slice.index]
            if len(horizon_slice) < max(pool_size, future_top_k) or not selected_symbols:
                row[f"h{horizon}_overlap_count"] = np.nan
                row[f"h{horizon}_precision"] = np.nan
                row[f"h{horizon}_recall"] = np.nan
                row[f"h{horizon}_leader_capture"] = np.nan
                row[f"h{horizon}_avg_future_rank"] = np.nan
                row[f"h{horizon}_pool_mean_future_return"] = np.nan
                continue

            future_top = horizon_slice.sort_values(future_return_col, ascending=False).head(future_top_k).index.tolist()
            overlap_count = len(pool_set & set(future_top))
            future_ranks = horizon_slice[future_return_col].rank(ascending=False, method="average")

            row[f"h{horizon}_overlap_count"] = overlap_count
            row[f"h{horizon}_precision"] = overlap_count / pool_size
            row[f"h{horizon}_recall"] = overlap_count / future_top_k
            row[f"h{horizon}_leader_capture"] = float(bool(future_top) and future_top[0] in pool_set)
            row[f"h{horizon}_avg_future_rank"] = float(future_ranks.loc[selected_symbols].mean())
            row[f"h{horizon}_pool_mean_future_return"] = float(
                horizon_slice.loc[selected_symbols, future_return_col].mean()
            )

        rows.append(row)
        previous_pool = pool_symbols

    return pd.DataFrame(rows)


def summarize_live_pool_shadow(shadow_table: pd.DataFrame) -> pd.DataFrame:
    """Collapse the detailed live-style shadow validation table into one summary row."""
    if shadow_table.empty:
        return pd.DataFrame(
            [
                {
                    "evaluation_dates": 0,
                }
            ]
        )

    numeric_means = shadow_table.select_dtypes(include=[np.number]).mean(numeric_only=True).to_dict()
    summary: dict[str, Any] = {
        "rebalance_frequency": shadow_table["rebalance_frequency"].dropna().iloc[0],
        "evaluation_dates": int(len(shadow_table)),
    }
    summary.update(numeric_means)
    return pd.DataFrame([summary])
