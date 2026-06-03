from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from .ranking import sort_ranking_snapshot
from .utils import date_to_str, write_json


DEFAULT_STRATEGY_PROFILE = "crypto_live_pool_rotation"
DEFAULT_ARTIFACT_TYPE = "live_pool"
DEFAULT_ARTIFACT_CONTRACT_VERSION = "crypto_live_pool_rotation.live_pool.v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_latest_universe(panel: pd.DataFrame, output_dir: str | Any, as_of_date: pd.Timestamp) -> dict[str, Any]:
    """Export the latest dynamic universe to JSON."""
    snapshot = panel.xs(as_of_date, level="date")
    symbols = sorted(snapshot.index[snapshot["in_universe"]].tolist())
    payload = {"as_of_date": date_to_str(as_of_date), "symbols": symbols}
    write_json(output_dir / "latest_universe.json", payload)
    return payload


def export_latest_ranking(panel: pd.DataFrame, output_dir: str | Any, as_of_date: pd.Timestamp) -> pd.DataFrame:
    """Export the latest ranking cross section to CSV."""
    snapshot = panel.xs(as_of_date, level="date").copy()
    snapshot = snapshot.loc[snapshot["in_universe"] | snapshot["selected_flag"]].copy()
    snapshot = sort_ranking_snapshot(snapshot)
    snapshot["as_of_date"] = date_to_str(as_of_date)
    snapshot["symbol"] = snapshot.index
    columns = [
        "as_of_date",
        "symbol",
        "rule_score",
        "linear_score",
        "ml_score",
        "final_score",
        "regime",
        "confidence",
        "selected_flag",
        "current_rank",
    ]
    exported = snapshot[columns].reset_index(drop=True)
    exported.to_csv(output_dir / "latest_ranking.csv", index=False)
    return exported


def _serialize_payload_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return date_to_str(value)
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def build_live_pool_payload(
    ranking_snapshot: pd.DataFrame,
    metadata: pd.DataFrame,
    as_of_date: pd.Timestamp,
    pool_size: int,
    mode: str = "core_major",
    source_project: str = "crypto-live-pool-pipelines",
    selection_meta_fields: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build additive live-pool payloads without performing I/O."""
    selected = sort_ranking_snapshot(ranking_snapshot).head(pool_size).copy()
    symbols = selected.index.tolist()
    metadata_indexed = metadata.set_index("symbol")
    as_of_date_str = date_to_str(as_of_date)
    version = f"{as_of_date_str}-{mode}"
    symbol_map = {
        symbol: {"base_asset": str(metadata_indexed.loc[symbol, "base_asset"])}
        for symbol in symbols
        if symbol in metadata_indexed.index
    }

    payload = {
        "as_of_date": as_of_date_str,
        "version": version,
        "mode": str(mode),
        "pool_size": len(symbols),
        "symbols": symbols,
        "symbol_map": symbol_map,
        "source_project": str(source_project),
    }
    legacy_payload = {
        "as_of_date": as_of_date_str,
        "version": version,
        "mode": str(mode),
        "pool_size": len(symbols),
        "symbols": symbol_map,
        "symbol_map": symbol_map,
        "source_project": str(source_project),
    }

    if selection_meta_fields:
        available_fields = [field for field in selection_meta_fields if field in selected.columns]
        selection_meta = {}
        for symbol in symbols:
            if symbol not in selected.index:
                continue
            meta = {}
            for field in available_fields:
                value = _serialize_payload_value(selected.loc[symbol, field])
                if value is None:
                    continue
                meta[field] = value
            if meta:
                selection_meta[symbol] = meta
        if selection_meta:
            payload["selection_meta"] = selection_meta
            legacy_payload["selection_meta"] = selection_meta

    return payload, legacy_payload


def export_live_pool(
    ranking_snapshot: pd.DataFrame,
    metadata: pd.DataFrame,
    output_dir: str | Any,
    as_of_date: pd.Timestamp,
    pool_size: int,
    mode: str = "core_major",
    source_project: str = "crypto-live-pool-pipelines",
    selection_meta_fields: list[str] | None = None,
    save_legacy: bool = True,
) -> dict[str, Any]:
    """Export the latest live pool in both simple and legacy-compatible forms."""
    payload, legacy_payload = build_live_pool_payload(
        ranking_snapshot=ranking_snapshot,
        metadata=metadata,
        as_of_date=as_of_date,
        pool_size=pool_size,
        mode=mode,
        source_project=source_project,
        selection_meta_fields=selection_meta_fields,
    )
    write_json(output_dir / "live_pool.json", payload)

    if save_legacy:
        write_json(output_dir / "live_pool_legacy.json", legacy_payload)
    return payload


def build_strategy_artifact_manifest(
    *,
    output_dir: str | Any,
    live_pool: dict[str, Any],
    strategy_profile: str = DEFAULT_STRATEGY_PROFILE,
    artifact_type: str = DEFAULT_ARTIFACT_TYPE,
    contract_version: str = DEFAULT_ARTIFACT_CONTRACT_VERSION,
    source_project: str = "crypto-live-pool-pipelines",
    generated_at: Any | None = None,
) -> dict[str, Any]:
    """Build the profile-aware artifact manifest consumed by downstream runtimes."""
    output_path = Path(output_dir)
    generated_at_value = generated_at if generated_at is not None else pd.Timestamp.now(tz="UTC")
    generated_at_text = (
        generated_at_value.isoformat()
        if hasattr(generated_at_value, "isoformat")
        else str(generated_at_value)
    )

    artifact_files = {
        "live_pool": "live_pool.json",
        "live_pool_legacy": "live_pool_legacy.json",
        "latest_ranking": "latest_ranking.csv",
        "latest_universe": "latest_universe.json",
    }
    artifacts = {}
    for artifact_name, filename in artifact_files.items():
        path = output_path / filename
        if not path.exists():
            continue
        artifacts[artifact_name] = {
            "path": filename,
            "sha256": _sha256_file(path),
        }

    symbols = live_pool.get("symbols", ())
    if isinstance(symbols, dict):
        symbols = tuple(symbols)
    elif isinstance(symbols, list):
        symbols = tuple(str(symbol) for symbol in symbols)
    else:
        symbols = ()

    as_of_date = str(live_pool.get("as_of_date", "")).strip()
    mode = str(live_pool.get("mode", "")).strip()
    version = str(live_pool.get("version", "")).strip()
    source_project_text = str(live_pool.get("source_project") or source_project)
    return {
        "manifest_type": "strategy_artifact",
        "contract_version": str(contract_version),
        "strategy_profile": str(strategy_profile),
        "artifact_type": str(artifact_type),
        "artifact_name": f"{strategy_profile}_{artifact_type}",
        "as_of_date": as_of_date,
        "snapshot_as_of": as_of_date,
        "version": version,
        "mode": mode,
        "symbol_count": len(symbols),
        "symbols": list(symbols),
        "source_project": source_project_text,
        "generated_at": generated_at_text,
        "primary_artifact": "live_pool",
        "artifacts": artifacts,
    }


def export_strategy_artifact_manifest(
    *,
    output_dir: str | Any,
    live_pool: dict[str, Any],
    strategy_profile: str = DEFAULT_STRATEGY_PROFILE,
    artifact_type: str = DEFAULT_ARTIFACT_TYPE,
    contract_version: str = DEFAULT_ARTIFACT_CONTRACT_VERSION,
    source_project: str = "crypto-live-pool-pipelines",
) -> dict[str, Any]:
    manifest = build_strategy_artifact_manifest(
        output_dir=output_dir,
        live_pool=live_pool,
        strategy_profile=strategy_profile,
        artifact_type=artifact_type,
        contract_version=contract_version,
        source_project=source_project,
    )
    write_json(Path(output_dir) / "artifact_manifest.json", manifest)
    return manifest
