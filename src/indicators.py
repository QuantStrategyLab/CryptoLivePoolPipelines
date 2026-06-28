from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Bitcoin genesis date for power-law age estimate
BITCOIN_GENESIS_DATE = pd.Timestamp("2009-01-03")


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def gma(series: pd.Series, window: int) -> pd.Series:
    """Geometric moving average."""
    log_values = np.log(series.clip(lower=1e-10))
    return np.exp(log_values.rolling(window=window, min_periods=window).mean())


# ---------------------------------------------------------------------------
# BTC cycle indicators — AHR999, Mayer Multiple, MVRV Z-Score proxy
# ---------------------------------------------------------------------------


def btc_power_law_estimate_price(as_of: pd.Timestamp) -> float:
    """Bitcoin age-based fair price estimate (power-law model).

    Formula: 10^(5.84 * log10(age_days) - 17.01)
    """
    age_days = max(1, (as_of.normalize() - BITCOIN_GENESIS_DATE).days)
    return float(10 ** (5.84 * math.log10(age_days) - 17.01))


def mayer_multiple(close: pd.Series, sma200: pd.Series | None = None) -> pd.Series:
    """Mayer Multiple: price / SMA200."""
    if sma200 is None:
        sma200 = close.rolling(200, min_periods=200).mean()
    return close / sma200.replace(0.0, np.nan)


def ahr999_index(
    close: pd.Series,
    *,
    sma200: pd.Series | None = None,
    window: int = 200,
) -> pd.Series:
    """AHR999 index: (price / GMA200) * (price / growth_estimate_price).

    The growth estimate is derived from a Bitcoin power-law age model.
    Low values (< 0.45) indicate bottom zone, high values (> 1.20) indicate
    expensive zone.
    """
    if sma200 is None:
        sma200 = close.rolling(window, min_periods=window).mean()
    gma200 = gma(close, window)

    def _compute_estimate(timestamp: pd.Timestamp) -> float:
        return btc_power_law_estimate_price(timestamp)

    estimate_series = close.index.map(_compute_estimate)
    estimate_series = pd.Series(estimate_series, index=close.index)

    # AHR999 = (price / GMA200) * (price / estimate)
    result = (close / gma200.replace(0.0, np.nan)) * (close / estimate_series.replace(0.0, np.nan))
    return result


def mvrv_zscore_proxy(
    close: pd.Series,
    sma200: pd.Series | None = None,
    window: int = 200,
) -> pd.Series:
    """Approximate MVRV Z-Score using on-chain proxy (Mayer Multiple deviation).

    Real MVRV Z-Score requires on-chain data (market cap / realized cap).
    This proxy uses (Mayer Multiple - 1) scaled by rolling volatility as a
    stand-in when on-chain data is unavailable.

    Returns a z-score-like series where:
    - values > 7 suggest overvaluation (risk_reduced)
    - values > 9 suggest extreme overvaluation (risk_off)
    """
    mm = mayer_multiple(close, sma200)
    mm_mean = mm.rolling(window, min_periods=window).mean()
    mm_std = mm.rolling(window, min_periods=window).std()
    return (mm - mm_mean) / mm_std.replace(0.0, np.nan)


def compute_btc_cycle_indicators(
    btc_close: pd.Series,
    *,
    as_of_date: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Compute all BTC cycle indicators from a price series.

    Returns a dict suitable for export as btc_cycle_indicators.json,
    compatible with the derived_indicators contract expected by crypto_btc_dca.
    """
    sma200 = sma(btc_close, 200)
    gma200 = gma(btc_close, 200)
    ahr999 = ahr999_index(btc_close, sma200=sma200)
    mayer = mayer_multiple(btc_close, sma200=sma200)
    zscore = mvrv_zscore_proxy(btc_close, sma200=sma200)

    if as_of_date is None:
        as_of_date = btc_close.index[-1]

    latest_idx = btc_close.index.get_indexer([as_of_date], method="pad")[0]
    if latest_idx < 0:
        latest_idx = len(btc_close) - 1
    latest_ts = btc_close.index[latest_idx]

    estimate_price = btc_power_law_estimate_price(latest_ts)
    high252 = float(btc_close.iloc[-252:].max())

    result: dict[str, Any] = {
        "as_of_date": str(latest_ts.date()),
        "close": float(btc_close.loc[latest_ts]),
        "sma200": float(sma200.loc[latest_ts]) if not pd.isna(sma200.loc[latest_ts]) else None,
        "gma200": float(gma200.loc[latest_ts]) if not pd.isna(gma200.loc[latest_ts]) else None,
        "high252": high252,
        "ahr999": float(ahr999.loc[latest_ts]) if not pd.isna(ahr999.loc[latest_ts]) else None,
        "ahr999_gma": float(ahr999.loc[latest_ts]) if not pd.isna(ahr999.loc[latest_ts]) else None,
        "mayer_multiple": float(mayer.loc[latest_ts]) if not pd.isna(mayer.loc[latest_ts]) else None,
        "mvrv_zscore": float(zscore.loc[latest_ts]) if not pd.isna(zscore.loc[latest_ts]) else None,
        "ahr999_estimate_price": estimate_price,
        "drawdown_252d": float(1.0 - btc_close.loc[latest_ts] / high252) if high252 > 0 else 0.0,
        "sma200_gap": float(btc_close.loc[latest_ts] / sma200.loc[latest_ts] - 1.0)
        if not pd.isna(sma200.loc[latest_ts]) and sma200.loc[latest_ts] > 0 else 0.0,
    }
    return result


def rate_of_change(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods)


def annualized_volatility(series: pd.Series, window: int, periods_per_year: int = 365) -> pd.Series:
    return series.rolling(window, min_periods=window).std() * np.sqrt(periods_per_year)


def downside_volatility(series: pd.Series, window: int, periods_per_year: int = 365) -> pd.Series:
    downside = series.where(series < 0.0, 0.0)
    return downside.rolling(window, min_periods=window).std() * np.sqrt(periods_per_year)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    previous_close = close.shift(1)
    components = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    )
    return components.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return true_range(high, low, close).rolling(window, min_periods=window).mean()


def rolling_drawdown(series: pd.Series, window: int = 180) -> pd.Series:
    rolling_peak = series.rolling(window, min_periods=1).max()
    return series / rolling_peak - 1.0


def ulcer_index(series: pd.Series, window: int = 50) -> pd.Series:
    rolling_peak = series.rolling(window, min_periods=window).max()
    drawdown_pct = 100.0 * (series / rolling_peak - 1.0)
    return np.sqrt((drawdown_pct.pow(2)).rolling(window, min_periods=window).mean())


def rolling_zscore(series: pd.Series, window: int = 120) -> pd.Series:
    mean = series.rolling(window, min_periods=window).mean()
    std = series.rolling(window, min_periods=window).std()
    return (series - mean) / std.replace(0.0, np.nan)


def rolling_beta(asset_returns: pd.Series, benchmark_returns: pd.Series, window: int = 60) -> pd.Series:
    covariance = asset_returns.rolling(window, min_periods=window).cov(benchmark_returns)
    variance = benchmark_returns.rolling(window, min_periods=window).var()
    return covariance / variance.replace(0.0, np.nan)


def rolling_correlation(asset_returns: pd.Series, benchmark_returns: pd.Series, window: int = 60) -> pd.Series:
    return asset_returns.rolling(window, min_periods=window).corr(benchmark_returns)

