from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def select_portfolio(
    snapshot: pd.DataFrame,
    score_column: str,
    top_n: int,
    weighting: str,
) -> pd.DataFrame:
    """Select a top-N portfolio from a single-date ranking snapshot."""
    eligible = snapshot.loc[snapshot["in_universe"] & snapshot[score_column].notna()].copy()
    if eligible.empty:
        return eligible

    weighting = weighting.lower()
    if weighting in {"inverse_vol", "inverse-vol", "inv_vol"} and "vol20" in eligible.columns:
        # Missing risk estimates cannot produce a trusted inverse-vol weight.
        # Exclude those candidates before top-N selection so one bad row does
        # not turn every selected target weight into NaN.
        eligible = eligible.loc[np.isfinite(eligible["vol20"])]
    selected = eligible.sort_values(score_column, ascending=False).head(top_n).copy()
    if selected.empty:
        return selected

    if weighting in {"inverse_vol", "inverse-vol", "inv_vol"} and "vol20" in selected.columns:
        # Compute normalized weights in float64 so downcast research inputs
        # cannot make a fully invested portfolio round above the strict guard.
        inverse_vol = 1.0 / selected["vol20"].astype(np.float64).clip(lower=0.05)
        selected["target_weight"] = inverse_vol / inverse_vol.sum()
    else:
        selected["target_weight"] = 1.0 / len(selected)
    return selected


def build_weight_vector(selected: pd.DataFrame, symbols: Iterable[str]) -> pd.Series:
    """Convert a selected portfolio dataframe into a full weight vector."""
    weights = pd.Series(0.0, index=pd.Index(sorted(set(symbols))), dtype=float)
    if selected.empty:
        return weights
    weights.loc[selected.index] = selected["target_weight"].astype(float)
    return weights


def calculate_turnover(previous_weights: pd.Series, next_weights: pd.Series) -> float:
    """Calculate one-way turnover between two fully invested weight vectors."""
    previous_weights = previous_weights.reindex(next_weights.index).fillna(0.0)
    next_weights = next_weights.reindex(previous_weights.index).fillna(0.0)
    return float(0.5 * (next_weights - previous_weights).abs().sum())
