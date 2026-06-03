from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .export import build_live_pool_payload
from .ranking import latest_ranking_snapshot
from .utils import date_to_str, ensure_directory, make_schedule, next_trading_date, write_json


def build_shadow_release_history(
    panel: pd.DataFrame,
    metadata: pd.DataFrame,
    config: dict[str, Any],
    output_dir: Path | str,
    *,
    cadence: str = "monthly",
    activation_lag_days: int = 1,
    selection_meta_fields: list[str] | None = None,
    profile_name: str | None = None,
    source_track: str | None = None,
    candidate_status: str | None = None,
) -> pd.DataFrame:
    """Build a local versioned artifact history for end-to-end shadow replay."""
    output_path = ensure_directory(output_dir)
    available_dates = list(panel.index.get_level_values("date").unique().sort_values())
    release_dates = make_schedule(available_dates, cadence)
    live_mode = str(
        config.get("publish", {}).get("mode")
        or config.get("universe", {}).get("live_mode")
        or "core_major"
    )
    source_project = str(
        config.get("publish", {}).get("source_project")
        or config.get("project", {}).get("name")
        or "crypto-live-pool-pipelines"
    )
    pool_size = int(config["export"]["live_pool_size"])

    rows: list[dict[str, Any]] = []
    previous_symbols: list[str] | None = None
    for release_date in release_dates:
        snapshot = latest_ranking_snapshot(panel, release_date)
        eligible = snapshot.loc[snapshot["in_universe"] & snapshot["final_score"].notna()].copy()
        if len(eligible) < pool_size:
            continue

        release_regime = None
        release_regime_confidence = np.nan
        if "regime" in snapshot.columns:
            regime_values = snapshot["regime"].dropna().astype(str)
            if not regime_values.empty:
                release_regime = regime_values.iloc[0]
        if "regime_confidence" in snapshot.columns:
            confidence_values = pd.to_numeric(snapshot["regime_confidence"], errors="coerce").dropna()
            if not confidence_values.empty:
                release_regime_confidence = float(confidence_values.iloc[0])

        payload, legacy_payload = build_live_pool_payload(
            ranking_snapshot=eligible,
            metadata=metadata,
            as_of_date=release_date,
            pool_size=pool_size,
            mode=live_mode,
            source_project=source_project,
            selection_meta_fields=selection_meta_fields,
        )
        version = str(payload["version"])
        release_dir = ensure_directory(output_path / version)
        live_pool_path = release_dir / "live_pool.json"
        legacy_path = release_dir / "live_pool_legacy.json"

        activation_date = next_trading_date(available_dates, release_date, lag_days=max(0, int(activation_lag_days)))
        if activation_date is None:
            activation_date = pd.Timestamp(release_date).normalize()

        track_metadata = {
            "profile": profile_name,
            "source_track": source_track,
            "candidate_status": candidate_status,
            "activation_date": date_to_str(activation_date),
            "expected_pool_size": int(pool_size),
        }
        payload.update({key: value for key, value in track_metadata.items() if value is not None})
        legacy_payload.update({key: value for key, value in track_metadata.items() if value is not None})

        write_json(live_pool_path, payload)
        write_json(legacy_path, legacy_payload)

        symbols = list(payload["symbols"])
        overlap = len(set(symbols) & set(previous_symbols or []))
        stability = np.nan if previous_symbols is None or not symbols else overlap / len(symbols)
        churn = np.nan if previous_symbols is None or not symbols else 1.0 - stability

        manifest = {
            "version": version,
            "as_of_date": payload["as_of_date"],
            "activation_date": date_to_str(activation_date),
            "mode": payload["mode"],
            "source_project": payload["source_project"],
            "pool_size": payload["pool_size"],
            "expected_pool_size": int(pool_size),
            "symbols": symbols,
            "profile": profile_name,
            "source_track": source_track,
            "candidate_status": candidate_status,
            "regime": release_regime,
            "regime_confidence": None if pd.isna(release_regime_confidence) else release_regime_confidence,
            "selection_meta_fields": list(selection_meta_fields or []),
            "artifacts": {
                "live_pool": str(live_pool_path.relative_to(output_path)),
                "live_pool_legacy": str(legacy_path.relative_to(output_path)),
            },
        }
        manifest_path = release_dir / "release_manifest.json"
        write_json(manifest_path, manifest)

        rows.append(
            {
                "version": version,
                "as_of_date": payload["as_of_date"],
                "activation_date": date_to_str(activation_date),
                "mode": payload["mode"],
                "source_project": payload["source_project"],
                "pool_size": payload["pool_size"],
                "expected_pool_size": int(pool_size),
                "symbols": "|".join(symbols),
                "profile": profile_name,
                "source_track": source_track,
                "candidate_status": candidate_status,
                "regime": release_regime,
                "regime_confidence": release_regime_confidence,
                "pool_stability": stability,
                "pool_churn": churn,
                "has_selection_meta": bool(payload.get("selection_meta")),
                "live_pool_path": str(live_pool_path.relative_to(output_path)),
                "live_pool_legacy_path": str(legacy_path.relative_to(output_path)),
                "release_manifest_path": str(manifest_path.relative_to(output_path)),
            }
        )
        previous_symbols = symbols

    index_table = pd.DataFrame(rows)
    if not index_table.empty:
        index_table = index_table.sort_values("as_of_date").reset_index(drop=True)
    index_table.to_csv(output_path / "release_index.csv", index=False)
    return index_table


def summarize_shadow_release_history(index_table: pd.DataFrame) -> pd.DataFrame:
    """Summarize the locally generated shadow release history."""
    if index_table.empty:
        return pd.DataFrame([{"release_count": 0}])

    summary = {
        "release_count": int(len(index_table)),
        "first_as_of_date": str(index_table["as_of_date"].iloc[0]),
        "last_as_of_date": str(index_table["as_of_date"].iloc[-1]),
        "mean_pool_size": float(index_table["pool_size"].mean()),
        "mean_pool_stability": float(index_table["pool_stability"].dropna().mean()),
        "mean_pool_churn": float(index_table["pool_churn"].dropna().mean()),
        "selection_meta_coverage": float(index_table["has_selection_meta"].astype(float).mean()),
    }
    return pd.DataFrame([summary])
