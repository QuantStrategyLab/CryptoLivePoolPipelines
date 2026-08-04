from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .backtest import run_backtest_suite, run_walkforward_scoring
from .evaluation import evaluate_leader_selection, leader_metrics_to_frame
from .external_data import load_optional_market_cap_metadata, merge_histories_with_external
from .export import (
    export_btc_cycle_indicators,
    export_latest_ranking,
    export_latest_universe,
    export_live_pool,
    export_strategy_artifact_manifest,
)
from .indicators import compute_btc_cycle_indicators
from .features import MODEL_FEATURE_COLUMNS, add_market_context_features, build_feature_panel
from .labels import build_labels
from .models import fit_predict_models
from .plots import save_equity_curve_plot, save_leader_metrics_plot
from .ranking import build_final_scores, latest_ranking_snapshot
from .regime import classify_regime
from .rules import compute_rule_scores
from .universe import build_dynamic_universe, filter_metadata_candidates, resolve_universe_mode
from .utils import get_logger, load_local_histories, read_json


def _build_live_prefilter_stats(
    raw_dir: Path,
    candidate_symbols: list[str],
    start_date: Any,
    end_date: Any,
    min_daily_quote_vol: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    end_timestamp = pd.Timestamp(end_date).normalize() if end_date is not None else None
    start_timestamp = pd.Timestamp(start_date).normalize() if start_date is not None else None

    for symbol in candidate_symbols:
        file_path = raw_dir / f"{symbol}.csv"
        if not file_path.exists():
            continue
        try:
            history = pd.read_csv(file_path, usecols=["date", "close", "quote_volume"])
        except Exception:
            continue
        if history.empty:
            continue

        history["date"] = pd.to_datetime(history["date"]).dt.normalize()
        if start_timestamp is not None:
            history = history.loc[history["date"] >= start_timestamp]
        if end_timestamp is not None:
            history = history.loc[history["date"] <= end_timestamp]
        if history.empty:
            continue

        quote_volume = pd.to_numeric(history["quote_volume"], errors="coerce")
        close = pd.to_numeric(history["close"], errors="coerce")
        history_days = int(len(history))

        tail_30 = quote_volume.tail(min(30, history_days))
        tail_90 = quote_volume.tail(min(90, history_days))
        tail_180 = quote_volume.tail(min(180, history_days))
        max_liquidity = max(tail_30.mean(), tail_90.mean(), tail_180.mean())
        min_liquidity = min(tail_30.mean(), tail_90.mean(), tail_180.mean())
        tradable_flag = ((quote_volume > 0.0) & close.notna()).astype(float)
        tradable_ratio_180 = float(tradable_flag.tail(min(180, history_days)).mean())

        if min_daily_quote_vol > 0.0:
            liquidity_days_90 = int((tail_90 >= min_daily_quote_vol).sum())
            liquidity_days_180 = int((tail_180 >= min_daily_quote_vol).sum())
        else:
            liquidity_days_90 = int(len(tail_90))
            liquidity_days_180 = int(len(tail_180))

        rows.append(
            {
                "symbol": symbol,
                "history_days": history_days,
                "latest_date": pd.to_datetime(history["date"]).max(),
                "avg_quote_vol_30": float(tail_30.mean()),
                "avg_quote_vol_90": float(tail_90.mean()),
                "avg_quote_vol_180": float(tail_180.mean()),
                "liquidity_stability": float(min_liquidity / max_liquidity) if max_liquidity > 0 else 0.0,
                "tradable_ratio_180": tradable_ratio_180,
                "liquidity_days_90": liquidity_days_90,
                "liquidity_days_180": liquidity_days_180,
            }
        )

    return pd.DataFrame(rows)


def _load_previous_live_symbols(output_dir: Path) -> list[str]:
    legacy_payload = read_json(output_dir / "live_pool_legacy.json", default={}) or {}
    symbols = legacy_payload.get("symbols", {})
    if isinstance(symbols, dict):
        return [symbol.upper() for symbol in symbols if isinstance(symbol, str) and symbol.endswith("USDT")]
    return []


def select_live_candidate_symbols(
    config: dict[str, Any],
    metadata: pd.DataFrame,
    as_of_date: Optional[pd.Timestamp] = None,
    universe_mode: Optional[str] = None,
) -> list[str]:
    """Select a compact live-build candidate set before loading full local histories."""
    logger = get_logger("live_prefilter")
    live_cfg = config.get("live_build", {})
    if not live_cfg.get("prefilter_enabled", True):
        return []

    resolved_mode, mode_cfg = resolve_universe_mode(config, universe_mode=universe_mode, purpose="live")
    filtered_metadata = filter_metadata_candidates(
        metadata,
        config,
        universe_mode=resolved_mode,
        purpose="live",
    )
    benchmark_symbol = str(config["data"]["benchmark_symbol"]).upper()
    metadata_candidates = sorted(
        set(filtered_metadata.loc[filtered_metadata["metadata_eligible"], "symbol"].astype(str).str.upper())
    )
    if not metadata_candidates:
        return [benchmark_symbol]

    stats = _build_live_prefilter_stats(
        raw_dir=config["paths"].raw_dir,
        candidate_symbols=metadata_candidates,
        start_date=config["data"]["start_date"],
        end_date=as_of_date or config["data"]["end_date"],
        min_daily_quote_vol=float(mode_cfg.get("min_daily_quote_vol", 0.0) or 0.0),
    )
    available_local_count = int(len(stats))
    if stats.empty:
        return [benchmark_symbol]

    history_ratio = float(live_cfg.get("history_ratio", 0.9))
    volume_ratio = float(live_cfg.get("volume_ratio", 0.8))
    stability_ratio = float(live_cfg.get("liquidity_stability_ratio", 0.9))
    tradable_ratio = float(live_cfg.get("tradable_ratio", 0.98))
    liquidity_days_ratio = float(live_cfg.get("liquidity_days_ratio", 0.9))

    stats["relaxed_pass"] = (
        (stats["history_days"] >= int(mode_cfg["min_history_days"] * history_ratio))
        & (stats["avg_quote_vol_30"] >= float(mode_cfg["min_avg_quote_vol_30"]) * volume_ratio)
        & (stats["avg_quote_vol_90"] >= float(mode_cfg["min_avg_quote_vol_90"]) * volume_ratio)
        & (stats["avg_quote_vol_180"] >= float(mode_cfg["min_avg_quote_vol_180"]) * volume_ratio)
        & (stats["liquidity_stability"] >= float(mode_cfg["min_liquidity_stability"]) * stability_ratio)
        & (stats["tradable_ratio_180"] >= float(mode_cfg["min_tradable_ratio_180"]) * tradable_ratio)
        & (
            stats["liquidity_days_90"]
            >= int(float(mode_cfg.get("min_liquidity_days_90", 0) or 0) * liquidity_days_ratio)
        )
        & (
            stats["liquidity_days_180"]
            >= int(float(mode_cfg.get("min_liquidity_days_180", 0) or 0) * liquidity_days_ratio)
        )
    )

    ranked = stats.sort_values(
        by=[
            "relaxed_pass",
            "avg_quote_vol_180",
            "avg_quote_vol_90",
            "avg_quote_vol_30",
            "liquidity_stability",
            "tradable_ratio_180",
            "history_days",
            "symbol",
        ],
        ascending=[False, False, False, False, False, False, False, True],
    ).reset_index(drop=True)
    ranked_symbols = ranked["symbol"].tolist()

    selected = set(ranked.loc[ranked["relaxed_pass"], "symbol"].tolist())
    min_candidate_count = int(live_cfg.get("min_candidate_count", 20))
    max_candidate_count = int(live_cfg.get("max_candidate_count", 30))
    target_count = max(min_candidate_count, len(selected))
    target_count = min(max(target_count, 1), max_candidate_count, len(ranked_symbols))
    selected.update(ranked_symbols[:target_count])

    if live_cfg.get("include_previous_live_pool", True):
        selected.update(_load_previous_live_symbols(config["paths"].output_dir))

    selected.add(benchmark_symbol)
    compact_candidates = [symbol for symbol in ranked_symbols if symbol in selected]
    compact_candidates.extend(
        sorted(symbol for symbol in selected if symbol not in set(compact_candidates) and symbol != benchmark_symbol)
    )
    if benchmark_symbol not in compact_candidates:
        compact_candidates.insert(0, benchmark_symbol)
    else:
        compact_candidates = [benchmark_symbol, *[symbol for symbol in compact_candidates if symbol != benchmark_symbol]]

    logger.info(
        "Live prefilter reduced %s metadata-eligible symbols (%s locally cached) to %s load symbols for mode '%s'.",
        len(metadata_candidates),
        available_local_count,
        len(compact_candidates),
        resolved_mode,
    )
    logger.info(
        "Live prefilter head:\n%s",
        ranked[
            [
                "symbol",
                "relaxed_pass",
                "history_days",
                "avg_quote_vol_180",
                "avg_quote_vol_90",
                "avg_quote_vol_30",
                "liquidity_stability",
            ]
        ]
        .head(max(target_count, 15))
        .to_string(index=False),
    )
    return compact_candidates


def prepare_research_panel(
    config: dict[str, Any],
    as_of_date: Optional[pd.Timestamp] = None,
    symbols: Optional[list[str]] = None,
    universe_mode: Optional[str] = None,
    purpose: str = "research",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load local raw data and build the full research panel."""
    logger = get_logger("prepare_research_panel")
    resolved_mode, _ = resolve_universe_mode(config, universe_mode=universe_mode, purpose=purpose)
    paths = config["paths"]
    metadata_path = Path(paths.cache_dir) / "symbol_metadata.csv"
    if not metadata_path.exists():
        raise FileNotFoundError(
            "Missing cached symbol metadata. Run scripts/download_history.py first."
        )
    metadata = pd.read_csv(metadata_path)
    start_date = config["data"]["start_date"]
    end_date = as_of_date or config["data"]["end_date"]
    load_symbols = list(symbols) if symbols is not None else None
    if purpose == "live":
        live_candidates = select_live_candidate_symbols(
            config,
            metadata,
            as_of_date=as_of_date,
            universe_mode=resolved_mode,
        )
        if load_symbols is None:
            load_symbols = live_candidates
        else:
            live_candidate_set = set(live_candidates)
            load_symbols = [symbol for symbol in load_symbols if symbol in live_candidate_set]
            benchmark_symbol = str(config["data"]["benchmark_symbol"]).upper()
            if benchmark_symbol not in load_symbols:
                load_symbols.insert(0, benchmark_symbol)
    histories = load_local_histories(paths.raw_dir, symbols=load_symbols, start_date=start_date, end_date=end_date)
    if not histories:
        raise FileNotFoundError("No local raw symbol histories were found. Run scripts/download_history.py first.")
    histories, external_merge_summary = merge_histories_with_external(histories, config, as_of_date=as_of_date)
    market_cap_metadata = load_optional_market_cap_metadata(config)
    if not external_merge_summary.empty:
        external_merge_summary.to_csv(paths.reports_dir / "external_data_quality_report.csv", index=False)
        extended = int((external_merge_summary["merged_rows"] > external_merge_summary["binance_rows"]).sum())
        applied = int(external_merge_summary["merge_applied"].fillna(False).astype(bool).sum()) if "merge_applied" in external_merge_summary.columns else extended
        logger.info(
            "External history merge summary: %s symbols extended; %s symbols passed the external quality gate.",
            extended,
            applied,
        )
    if not market_cap_metadata.empty:
        logger.info("Loaded optional external market-cap metadata with %s rows.", len(market_cap_metadata))

    benchmark_symbol = config["data"]["benchmark_symbol"]
    if benchmark_symbol not in histories:
        raise FileNotFoundError(
            f"The benchmark symbol {benchmark_symbol} is missing from data/raw. Download it before research."
        )

    logger.info("Building base feature panel from %s local symbols.", len(histories))
    panel = build_feature_panel(histories, benchmark_symbol, config, as_of_date=as_of_date)
    panel = build_dynamic_universe(
        panel,
        metadata,
        config,
        universe_mode=resolved_mode,
        purpose=purpose,
        market_cap_metadata=market_cap_metadata,
    )
    panel = add_market_context_features(panel, config["feature_engineering"]["breadth_min_names"])
    panel = build_labels(panel, config)
    panel = compute_rule_scores(panel, config)
    panel = classify_regime(panel, config)
    return panel.sort_index(), metadata


def run_research_pipeline(config: dict[str, Any], universe_mode: Optional[str] = None) -> dict[str, Any]:
    """End-to-end research workflow used by the research and validation scripts."""
    logger = get_logger("run_research_pipeline")
    resolved_mode, _ = resolve_universe_mode(config, universe_mode=universe_mode, purpose="research")
    logger.info("Running research pipeline with universe mode '%s'.", resolved_mode)
    panel, metadata = prepare_research_panel(config, universe_mode=resolved_mode, purpose="research")
    feature_columns = [column for column in MODEL_FEATURE_COLUMNS if column in panel.columns]
    panel, window_summary = run_walkforward_scoring(panel, feature_columns, config)
    panel = build_final_scores(panel, config)

    backtests = run_backtest_suite(panel, config)
    leader_metrics = leader_metrics_to_frame(evaluate_leader_selection(panel, "final_score", config))

    reports_dir = config["paths"].reports_dir
    plots_cfg = config["plots"]
    save_equity_curve_plot(backtests, reports_dir / "equity_curves.png", plots_cfg["style"])
    save_leader_metrics_plot(leader_metrics, reports_dir / "leader_metrics.png", plots_cfg["style"])

    summary_rows = []
    for name, result in backtests.items():
        row = {"strategy": name}
        row.update(result.metrics)
        summary_rows.append(row)
    performance_table = pd.DataFrame(summary_rows)
    performance_table.to_csv(reports_dir / "performance_summary.csv", index=False)
    window_summary.to_csv(reports_dir / "walkforward_windows.csv", index=False)
    leader_metrics.to_csv(reports_dir / "leader_metrics.csv", index=False)
    logger.info("Saved reports into %s.", reports_dir)

    return {
        "panel": panel,
        "metadata": metadata,
        "window_summary": window_summary,
        "backtests": backtests,
        "leader_metrics": leader_metrics,
        "performance_table": performance_table,
        "universe_mode": resolved_mode,
    }


def build_live_pool_outputs(
    config: dict[str, Any],
    as_of_date: Optional[pd.Timestamp] = None,
    universe_mode: Optional[str] = None,
) -> dict[str, Any]:
    """Train on the latest eligible history and export live universe/ranking files."""
    if config["export"].get("save_legacy_live_pool", True) is False:
        raise ValueError(
            "export.save_legacy_live_pool=false is incompatible with the required "
            "four-artifact release contract."
        )

    logger = get_logger("build_live_pool_outputs")
    resolved_mode, _ = resolve_universe_mode(config, universe_mode=universe_mode, purpose="live")
    logger.info("Building live pool with universe mode '%s'.", resolved_mode)
    panel, metadata = prepare_research_panel(
        config,
        as_of_date=as_of_date,
        universe_mode=resolved_mode,
        purpose="live",
    )
    available_dates = list(panel.index.get_level_values("date").unique().sort_values())
    if as_of_date is None:
        latest_date = available_dates[-1]
    else:
        requested_date = pd.Timestamp(as_of_date)
        eligible_dates = [date for date in available_dates if date <= requested_date]
        if not eligible_dates:
            raise ValueError(f"No local data is available on or before {requested_date.date()}.")
        latest_date = max(eligible_dates)

    horizons = [int(h) for h in config["labels"]["horizons"]]
    max_horizon = max(horizons)
    latest_position = available_dates.index(latest_date)
    train_end_position = max(0, latest_position - max_horizon)
    train_end_date = available_dates[train_end_position]
    train_start_position = max(0, train_end_position - int(config["walkforward"]["train_window_days"]) + 1)
    train_start_date = available_dates[train_start_position]

    feature_columns = [column for column in MODEL_FEATURE_COLUMNS if column in panel.columns]
    date_index = panel.index.get_level_values("date")
    train_mask = (
        (date_index >= train_start_date)
        & (date_index <= train_end_date)
        & panel["in_universe"]
        & panel["blended_target"].notna()
    )
    score_mask = (date_index == latest_date) & panel["in_universe"]
    result = fit_predict_models(panel.loc[train_mask], panel.loc[score_mask], feature_columns, config)

    if result.predictions.empty:
        panel.loc[score_mask, "linear_score_raw"] = pd.NA
        panel.loc[score_mask, "ml_score_raw"] = pd.NA
    else:
        panel = panel.join(result.predictions, how="left")
    panel = build_final_scores(panel, config)
    input_timestamp = resolve_scoring_input_timestamp(panel, score_mask)

    output_dir = config["paths"].output_dir
    export_latest_universe(panel, output_dir, latest_date)
    export_latest_ranking(panel, output_dir, latest_date)
    latest_snapshot = latest_ranking_snapshot(panel, latest_date)
    source_project = str(
        config.get("publish", {}).get(
            "source_project",
            config.get("project", {}).get("name", "crypto-live-pool-pipelines"),
        )
    )
    live_payload = export_live_pool(
        ranking_snapshot=latest_snapshot.loc[latest_snapshot["selected_flag"] | latest_snapshot["in_universe"]],
        metadata=metadata,
        output_dir=output_dir,
        as_of_date=latest_date,
        pool_size=int(config["export"]["live_pool_size"]),
        mode=str(resolved_mode),
        source_project=source_project,
        selection_meta_fields=(
            list(config["export"].get("selection_meta_fields", []))
            if config["export"].get("include_selection_meta", False)
            else None
        ),
        save_legacy=bool(config["export"]["save_legacy_live_pool"]),
    )
    artifact_manifest = export_strategy_artifact_manifest(
        output_dir=output_dir,
        live_pool=live_payload,
        source_project=source_project,
        input_timestamp=input_timestamp,
    )

    # Compute and export BTC cycle indicators for crypto DCA strategies
    btc_cycle = {}
    benchmark_symbol = str(config.get("data", {}).get("benchmark_symbol", "BTCUSDT"))
    btc_file = Path(config["paths"].raw_dir) / f"{benchmark_symbol}.csv"
    if btc_file.exists():
        try:
            btc_close = pd.read_csv(btc_file, index_col=0, parse_dates=True).squeeze("columns")
            if isinstance(btc_close, pd.DataFrame):
                btc_close = btc_close.iloc[:, 0]
            btc_close = btc_close.sort_index()
            btc_cycle = compute_btc_cycle_indicators(btc_close, as_of_date=latest_date)
            export_btc_cycle_indicators(btc_cycle, output_dir)
            logger.info("BTC cycle indicators exported: AHR999=%.3f Mayer=%.3f",
                         btc_cycle.get("ahr999", float("nan")),
                         btc_cycle.get("mayer_multiple", float("nan")))
        except Exception as exc:
            logger.warning("BTC cycle indicators unavailable: %s", exc)
    else:
        logger.debug("BTC price file not found at %s, skipping cycle indicators", btc_file)

    logger.info("Live pool exports saved into %s for %s.", output_dir, latest_date.date())

    return {
        "panel": panel,
        "metadata": metadata,
        "live_payload": live_payload,
        "artifact_manifest": artifact_manifest,
        "btc_cycle_indicators": btc_cycle,
        "as_of_date": latest_date,
        "train_start_date": train_start_date,
        "train_end_date": train_end_date,
        "linear_backend": result.linear_backend,
        "ml_backend": result.ml_backend,
        "universe_mode": resolved_mode,
    }


def resolve_scoring_input_timestamp(panel: pd.DataFrame, score_mask: Any) -> pd.Timestamp:
    """Return the maximum date among rows admitted to final scoring/export."""
    scoring_rows = panel.loc[score_mask]
    if scoring_rows.empty:
        raise ValueError("No panel rows were admitted to final scoring/export.")
    dates = pd.to_datetime(scoring_rows.index.get_level_values("date"), errors="coerce")
    if dates.isna().any():
        raise ValueError("Final scoring/export rows contain an invalid date.")
    return pd.Timestamp(dates.max()).normalize()
