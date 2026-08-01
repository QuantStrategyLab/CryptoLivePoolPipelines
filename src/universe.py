from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from .utils import make_schedule


def _make_universe_refresh_schedule(
    dates: list[pd.Timestamp],
    frequency: str,
) -> list[pd.Timestamp]:
    """Prevent adjacent month-end/next-month snapshots from counting twice."""
    refresh_dates = make_schedule(dates, frequency)
    if frequency.lower() == "monthly" and len(refresh_dates) >= 2:
        previous_refresh = pd.Timestamp(refresh_dates[-2])
        latest_refresh = pd.Timestamp(refresh_dates[-1])
        if (
            latest_refresh.normalize() == previous_refresh.normalize() + pd.Timedelta(days=1)
            and latest_refresh.month != previous_refresh.month
        ):
            refresh_dates.pop(-2)
    return refresh_dates


def resolve_universe_mode(
    config: dict[str, Any],
    universe_mode: str | None = None,
    purpose: str = "research",
) -> tuple[str, dict[str, Any]]:
    """Resolve a named universe mode from config with sensible per-purpose defaults."""
    universe_cfg = config["universe"]
    mode_name = universe_mode or universe_cfg.get(f"{purpose}_mode") or universe_cfg.get("research_mode")
    modes = universe_cfg.get("modes", {})
    if mode_name not in modes:
        available = ", ".join(sorted(modes)) or "<none>"
        raise KeyError(f"Unknown universe mode '{mode_name}'. Available modes: {available}")
    return str(mode_name), dict(modes[mode_name])


def filter_metadata_candidates(
    metadata: pd.DataFrame,
    config: dict[str, Any],
    universe_mode: str | None = None,
    purpose: str = "research",
) -> pd.DataFrame:
    """Apply static metadata filters before any history-based screening."""
    universe_cfg = config["universe"]
    _, mode_cfg = resolve_universe_mode(config, universe_mode=universe_mode, purpose=purpose)
    metadata = metadata.copy()
    metadata["symbol"] = metadata["symbol"].str.upper()
    metadata["base_asset"] = metadata["base_asset"].str.upper()
    metadata["quote_asset"] = metadata["quote_asset"].str.upper()

    excluded_bases = {item.upper() for item in universe_cfg["exclude_base_assets"]}
    excluded_symbols = {item.upper() for item in universe_cfg.get("exclude_symbols", [])}
    excluded_bases.update({item.upper() for item in mode_cfg.get("exclude_base_assets_extra", [])})
    excluded_symbols.update({item.upper() for item in mode_cfg.get("exclude_symbols_extra", [])})
    if mode_cfg.get("exclude_high_noise_assets", False):
        excluded_bases.update({item.upper() for item in universe_cfg.get("high_noise_base_assets", [])})
        excluded_symbols.update({item.upper() for item in universe_cfg.get("high_noise_symbols", [])})
    suffix_keywords = [keyword.upper() for keyword in universe_cfg["exclude_suffix_keywords"]]
    allowed_quotes = {item.upper() for item in universe_cfg["allowed_quote_assets"]}
    benchmark_symbols = {item.upper() for item in universe_cfg.get("include_benchmark_symbols", [])}

    metadata["metadata_eligible"] = (
        metadata["status"].eq("TRADING")
        & metadata["quote_asset"].isin(allowed_quotes)
        & metadata["is_spot_trading_allowed"].astype(bool)
        & ~metadata["base_asset"].isin(excluded_bases)
        & ~metadata["symbol"].isin(excluded_symbols)
    )

    has_bad_suffix = metadata["base_asset"].apply(
        lambda asset: any(keyword in asset for keyword in suffix_keywords)
    )
    metadata.loc[has_bad_suffix, "metadata_eligible"] = False
    metadata["is_benchmark"] = metadata["symbol"].isin(benchmark_symbols)
    return metadata


def build_dynamic_universe(
    panel: pd.DataFrame,
    metadata: pd.DataFrame,
    config: dict[str, Any],
    universe_mode: str | None = None,
    purpose: str = "research",
    market_cap_metadata: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Apply rolling liquidity/history constraints and monthly universe refresh logic."""
    universe_cfg = config["universe"]
    mode_name, mode_cfg = resolve_universe_mode(config, universe_mode=universe_mode, purpose=purpose)
    metadata_filtered = filter_metadata_candidates(
        metadata,
        config,
        universe_mode=mode_name,
        purpose=purpose,
    ).set_index("symbol")
    panel = panel.copy()

    symbols = panel.index.get_level_values("symbol")
    panel["metadata_eligible"] = symbols.map(metadata_filtered["metadata_eligible"]).fillna(False).astype(bool)
    panel["is_benchmark"] = symbols.map(metadata_filtered["is_benchmark"]).fillna(False).astype(bool)
    panel["universe_mode"] = mode_name
    panel["history_eligible"] = panel["age_days"] >= mode_cfg["min_history_days"]
    panel["liquidity_eligible"] = (
        (panel["avg_quote_vol_30"] >= mode_cfg["min_avg_quote_vol_30"])
        & (panel["avg_quote_vol_90"] >= mode_cfg["min_avg_quote_vol_90"])
        & (panel["avg_quote_vol_180"] >= mode_cfg["min_avg_quote_vol_180"])
        & (panel["liquidity_stability"] >= mode_cfg["min_liquidity_stability"])
    )
    panel["tradable_eligible"] = panel["tradable_ratio_180"] >= mode_cfg["min_tradable_ratio_180"]
    panel["market_cap_eligible"] = True

    external_cfg = config.get("external_data", {})
    if (
        external_cfg.get("enabled", False)
        and external_cfg.get("use_market_cap_filter", False)
        and market_cap_metadata is not None
        and not market_cap_metadata.empty
        and "symbol" in market_cap_metadata.columns
    ):
        market_cap_frame = market_cap_metadata.copy()
        market_cap_frame["symbol"] = market_cap_frame["symbol"].str.upper()
        market_cap_frame = market_cap_frame.drop_duplicates("symbol", keep="last").set_index("symbol")
        market_cap_eligible = pd.Series(True, index=market_cap_frame.index, dtype=bool)
        min_market_cap_usd = external_cfg.get("min_market_cap_usd")
        max_market_cap_rank = external_cfg.get("max_market_cap_rank")
        if min_market_cap_usd is not None and "market_cap_usd" in market_cap_frame.columns:
            market_cap_eligible &= pd.to_numeric(
                market_cap_frame["market_cap_usd"], errors="coerce"
            ).fillna(0.0) >= float(min_market_cap_usd)
        if max_market_cap_rank is not None and "market_cap_rank" in market_cap_frame.columns:
            market_cap_eligible &= pd.to_numeric(
                market_cap_frame["market_cap_rank"], errors="coerce"
            ).fillna(float("inf")) <= float(max_market_cap_rank)
        panel["market_cap_eligible"] = symbols.map(market_cap_eligible).fillna(False).astype(bool)

    min_daily_quote_vol = float(mode_cfg.get("min_daily_quote_vol", 0.0) or 0.0)
    min_liquidity_days_90 = int(mode_cfg.get("min_liquidity_days_90", 0) or 0)
    min_liquidity_days_180 = int(mode_cfg.get("min_liquidity_days_180", 0) or 0)
    if min_daily_quote_vol > 0.0:
        panel["liquidity_days_90"] = panel.groupby(level="symbol")["quote_volume"].transform(
            lambda series: (series >= min_daily_quote_vol).astype(float).rolling(90, min_periods=90).sum()
        )
        panel["liquidity_days_180"] = panel.groupby(level="symbol")["quote_volume"].transform(
            lambda series: (series >= min_daily_quote_vol).astype(float).rolling(180, min_periods=180).sum()
        )
    else:
        panel["liquidity_days_90"] = pd.NA
        panel["liquidity_days_180"] = pd.NA

    panel["continuous_liquidity_eligible"] = True
    if min_liquidity_days_90 > 0:
        panel["continuous_liquidity_eligible"] &= (
            pd.to_numeric(panel["liquidity_days_90"], errors="coerce").fillna(0.0) >= min_liquidity_days_90
        )
    if min_liquidity_days_180 > 0:
        panel["continuous_liquidity_eligible"] &= (
            pd.to_numeric(panel["liquidity_days_180"], errors="coerce").fillna(0.0) >= min_liquidity_days_180
        )

    panel["candidate_eligible"] = (
        panel["metadata_eligible"]
        & panel["history_eligible"]
        & panel["liquidity_eligible"]
        & panel["tradable_eligible"]
        & panel["continuous_liquidity_eligible"]
        & panel["market_cap_eligible"]
        & ~panel["is_benchmark"]
    )

    dates = list(panel.index.get_level_values("date").unique().sort_values())
    refresh_dates = _make_universe_refresh_schedule(dates, universe_cfg["refresh_frequency"])
    date_index = panel.index.get_level_values("date")
    symbol_index = panel.index.get_level_values("symbol")
    panel["in_universe"] = False
    panel["universe_snapshot_date"] = pd.NaT
    entry_confirmations = max(1, int(mode_cfg.get("entry_confirmations", 1)))
    exit_confirmations = max(1, int(mode_cfg.get("exit_confirmations", 1)))
    all_symbols = sorted(set(symbol_index.tolist()))
    eligible_streaks = defaultdict(int)
    ineligible_streaks = defaultdict(int)
    active_members: set[str] = set()

    for position, refresh_date in enumerate(refresh_dates):
        next_refresh = refresh_dates[position + 1] if position + 1 < len(refresh_dates) else None
        date_mask = date_index >= refresh_date
        if next_refresh is not None:
            date_mask &= date_index < next_refresh

        snapshot_candidates = panel.xs(refresh_date, level="date")
        current_candidates = set(snapshot_candidates.index[snapshot_candidates["candidate_eligible"]].tolist())
        for symbol in all_symbols:
            if symbol in current_candidates:
                eligible_streaks[symbol] += 1
                ineligible_streaks[symbol] = 0
            else:
                eligible_streaks[symbol] = 0
                ineligible_streaks[symbol] += 1

        next_active_members = {
            symbol for symbol in active_members if ineligible_streaks[symbol] < exit_confirmations
        }
        for symbol in current_candidates:
            if symbol in active_members or eligible_streaks[symbol] >= entry_confirmations:
                next_active_members.add(symbol)
        active_members = next_active_members
        panel.loc[date_mask, "universe_snapshot_date"] = refresh_date
        if active_members:
            membership_mask = date_mask & symbol_index.isin(sorted(active_members))
            panel.loc[membership_mask, "in_universe"] = True

    panel.loc[panel["is_benchmark"], "in_universe"] = False
    return panel


def latest_universe_snapshot(panel: pd.DataFrame, as_of_date: pd.Timestamp | str) -> list[str]:
    """Return the sorted universe symbols on a single snapshot date."""
    snapshot = panel.xs(pd.Timestamp(as_of_date), level="date")
    universe_symbols = snapshot.index[snapshot["in_universe"]].tolist()
    return sorted(universe_symbols)
