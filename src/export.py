from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
from typing import Any

import pandas as pd

from .model_run_manifest import (
    canonical_model_run_manifest_digest,
    validate_model_run_manifest,
    write_model_run_manifest,
)
from .ranking import sort_ranking_snapshot
from .utils import date_to_str, write_json


DEFAULT_STRATEGY_PROFILE = "crypto_live_pool_rotation"
DEFAULT_ARTIFACT_TYPE = "live_pool"
DEFAULT_ARTIFACT_CONTRACT_VERSION = "crypto_live_pool_rotation.live_pool.v1"
REQUIRED_IDENTITY_ARTIFACTS = (
    "live_pool",
    "live_pool_legacy",
    "latest_ranking",
    "latest_universe",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_clean_source_revision(repo_root: Path | None = None) -> str:
    """Resolve the commit whose clean tracked tree is executing this producer."""
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "diff", "--cached", "--quiet", "HEAD", "--"],
            cwd=root,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Cannot bind artifact bytes to a clean producer commit.") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError("Producer HEAD did not resolve to a lowercase 40-character commit.")
    return revision


def _normalize_input_timestamp(value: Any, *, as_of_date: str) -> str:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("input_timestamp must be a finite panel date.")
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    timestamp = timestamp.normalize()
    expected = pd.Timestamp(as_of_date).tz_localize("UTC")
    if timestamp != expected:
        raise ValueError("input_timestamp must equal live_pool.as_of_date at UTC midnight.")
    return timestamp.strftime("%Y-%m-%dT00:00:00Z")


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
    # Ranks are ordinal contract fields. Keep missing values nullable while
    # avoiding CSV values such as "5.0" that integer-oriented report readers
    # can misrender as rank zero.
    snapshot["current_rank"] = pd.to_numeric(snapshot["current_rank"], errors="raise").astype("Int64")
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
    input_timestamp: Any,
    generated_at: Any | None = None,
    model_run_manifest: dict[str, Any] | None = None,
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
        if not path.is_file():
            raise FileNotFoundError(f"Required identity artifact is missing: {path}")
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
    input_timestamp_text = _normalize_input_timestamp(input_timestamp, as_of_date=as_of_date)
    runtime_evidence_identity = {
        "strategy_profile": str(strategy_profile),
        "mode": mode,
        "source_revision": resolve_clean_source_revision(),
        "input_timestamp": input_timestamp_text,
        "artifact_contract": str(contract_version),
        "artifact_version": version,
        "artifacts": {name: dict(artifacts[name]) for name in REQUIRED_IDENTITY_ARTIFACTS},
    }
    payload = {
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
        "runtime_evidence_identity": runtime_evidence_identity,
    }
    if model_run_manifest is not None:
        validated_model_run_manifest = validate_model_run_manifest(model_run_manifest)
        if validated_model_run_manifest["source"]["revision"] != runtime_evidence_identity["source_revision"]:
            raise ValueError("model_run_manifest source revision must match runtime evidence identity.")
        model_run_digest = write_model_run_manifest(
            output_path / "model_run_manifest.json",
            validated_model_run_manifest,
        )
        if model_run_digest != canonical_model_run_manifest_digest(validated_model_run_manifest):
            raise ValueError("model_run_manifest canonical digest mismatch.")
        payload["model_run_manifest"] = validated_model_run_manifest
        runtime_evidence_identity["model_run_manifest"] = {
            "contract_version": validated_model_run_manifest["contract_version"],
            "path": "model_run_manifest.json",
            "sha256": model_run_digest,
        }
    return payload


def export_btc_cycle_indicators(
    btc_indicators: dict[str, Any],
    output_dir: str | Any,
) -> dict[str, Any]:
    """Export BTC cycle indicators for consumption by crypto DCA strategies.

    Produces btc_cycle_indicators.json with AHR999, Mayer Multiple,
    MVRV Z-Score proxy, and related metrics. Compatible with the
    derived_indicators contract expected by crypto_btc_dca.
    """
    write_json(output_dir / "btc_cycle_indicators.json", btc_indicators)
    return btc_indicators


def export_strategy_artifact_manifest(
    *,
    output_dir: str | Any,
    live_pool: dict[str, Any],
    strategy_profile: str = DEFAULT_STRATEGY_PROFILE,
    artifact_type: str = DEFAULT_ARTIFACT_TYPE,
    contract_version: str = DEFAULT_ARTIFACT_CONTRACT_VERSION,
    source_project: str = "crypto-live-pool-pipelines",
    input_timestamp: Any,
    model_run_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = build_strategy_artifact_manifest(
        output_dir=output_dir,
        live_pool=live_pool,
        strategy_profile=strategy_profile,
        artifact_type=artifact_type,
        contract_version=contract_version,
        source_project=source_project,
        input_timestamp=input_timestamp,
        model_run_manifest=model_run_manifest,
    )
    write_json(Path(output_dir) / "artifact_manifest.json", manifest)
    return manifest
