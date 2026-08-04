from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .utils import read_json


REQUIRED_OUTPUT_FILES = (
    "latest_universe.json",
    "latest_ranking.csv",
    "live_pool.json",
    "live_pool_legacy.json",
)
REQUIRED_MANIFEST_FILE = "release_manifest.json"
REQUIRED_ARTIFACT_MANIFEST_FILE = "artifact_manifest.json"
REQUIRED_RANKING_COLUMNS = (
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
)
REQUIRED_LIVE_POOL_FIELDS = (
    "as_of_date",
    "version",
    "mode",
    "pool_size",
    "symbols",
    "symbol_map",
    "source_project",
)
REQUIRED_ARTIFACT_MANIFEST_FIELDS = (
    "manifest_type",
    "contract_version",
    "strategy_profile",
    "artifact_type",
    "artifact_name",
    "as_of_date",
    "snapshot_as_of",
    "version",
    "mode",
    "symbol_count",
    "symbols",
    "source_project",
    "generated_at",
    "primary_artifact",
    "artifacts",
)
REQUIRED_ARTIFACT_MANIFEST_ARTIFACTS = {
    "latest_universe": "latest_universe.json",
    "latest_ranking": "latest_ranking.csv",
    "live_pool": "live_pool.json",
    "live_pool_legacy": "live_pool_legacy.json",
}
EXPECTED_ARTIFACT_CONTRACT_VERSION = "crypto_live_pool_rotation.live_pool.v1"
REQUIRED_RUNTIME_EVIDENCE_IDENTITY_FIELDS = (
    "strategy_profile",
    "mode",
    "source_revision",
    "input_timestamp",
    "artifact_contract",
    "artifact_version",
    "artifacts",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_version(as_of_date: str, mode: str) -> str:
    return f"{as_of_date}-{mode}"


def _parse_as_of_date(value: Any) -> tuple[str, pd.Timestamp | None]:
    if not isinstance(value, str) or not value.strip():
        return "", None
    try:
        parsed = pd.Timestamp(value[:10]).normalize()
    except Exception:
        return str(value).strip(), None
    return parsed.strftime("%Y-%m-%d"), parsed


def _append_missing_fields(payload: dict[str, Any], required_fields: tuple[str, ...], errors: list[str], label: str) -> None:
    for field in required_fields:
        if field not in payload:
            errors.append(f"{label} missing field: {field}")


def _validate_symbol(symbol: Any, field_label: str, errors: list[str]) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        errors.append(f"{field_label} contains an empty symbol")
        return ""
    normalized = symbol.strip().upper()
    if not normalized.endswith("USDT"):
        errors.append(f"{field_label} contains non-USDT symbol: {normalized}")
        return ""
    return normalized


def _normalize_symbol_list(symbols: Any, field_label: str, errors: list[str]) -> list[str]:
    if not isinstance(symbols, list) or not symbols:
        errors.append(f"{field_label} must be a non-empty list")
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = _validate_symbol(raw_symbol, field_label, errors)
        if not symbol:
            continue
        if symbol in seen:
            errors.append(f"{field_label} contains duplicate symbol: {symbol}")
            continue
        normalized.append(symbol)
        seen.add(symbol)
    return normalized


def _normalize_symbol_map(symbol_map: Any, field_label: str, errors: list[str]) -> dict[str, dict[str, str]]:
    if not isinstance(symbol_map, dict) or not symbol_map:
        errors.append(f"{field_label} must be a non-empty mapping")
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for raw_symbol, raw_meta in symbol_map.items():
        symbol = _validate_symbol(raw_symbol, field_label, errors)
        if not symbol:
            continue
        if not isinstance(raw_meta, dict):
            errors.append(f"{field_label}[{symbol}] must be an object")
            continue
        base_asset = str(raw_meta.get("base_asset", "")).strip().upper()
        if not base_asset:
            errors.append(f"{field_label}[{symbol}] missing base_asset")
            continue
        normalized[symbol] = {"base_asset": base_asset}
    return normalized


def _normalize_pool_size(value: Any, field_label: str, errors: list[str]) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        errors.append(f"{field_label} must be an integer")
        return None
    if parsed <= 0:
        errors.append(f"{field_label} must be positive")
        return None
    return parsed


def _normalize_source_project(value: Any, field_label: str, errors: list[str]) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        errors.append(f"{field_label} must be a non-empty string")
    return normalized


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value.strip()))


def _validate_runtime_evidence_identity(
    manifest: dict[str, Any],
    artifact_manifest: dict[str, Any],
    *,
    live_pool_mode: str,
    live_pool_version: str,
    errors: list[str],
) -> None:
    label = "release_manifest.json runtime_evidence_identity"
    identity = manifest.get("runtime_evidence_identity")
    if not isinstance(identity, dict):
        errors.append(f"{label} must be an object")
        return

    _append_missing_fields(identity, REQUIRED_RUNTIME_EVIDENCE_IDENTITY_FIELDS, errors, label)
    if str(identity.get("strategy_profile", "")).strip() != str(
        artifact_manifest.get("strategy_profile", "")
    ).strip():
        errors.append(f"{label} strategy_profile does not match artifact_manifest.json")
    if str(identity.get("mode", "")).strip() != live_pool_mode:
        errors.append(f"{label} mode does not match live_pool.json")
    if not re.fullmatch(r"[0-9a-f]{40}", str(identity.get("source_revision", "")).strip()):
        errors.append(f"{label} source_revision must be a 40-character lowercase git SHA")

    input_timestamp = identity.get("input_timestamp")
    try:
        timestamp = pd.Timestamp(input_timestamp)
    except Exception:
        timestamp = None
    if not isinstance(input_timestamp, str) or timestamp is None or pd.isna(timestamp) or timestamp.tzinfo is None:
        errors.append(f"{label} input_timestamp must be a timezone-aware timestamp")

    if str(identity.get("artifact_contract", "")).strip() != str(
        artifact_manifest.get("contract_version", "")
    ).strip():
        errors.append(f"{label} artifact_contract does not match artifact_manifest.json")
    if str(identity.get("artifact_version", "")).strip() != live_pool_version:
        errors.append(f"{label} artifact_version does not match live_pool.json version")

    identity_artifacts = identity.get("artifacts")
    artifact_manifest_artifacts = artifact_manifest.get("artifacts")
    if not isinstance(identity_artifacts, dict):
        errors.append(f"{label} artifacts must be an object")
        return
    if not isinstance(artifact_manifest_artifacts, dict):
        return
    for artifact_name in REQUIRED_ARTIFACT_MANIFEST_ARTIFACTS:
        identity_entry = identity_artifacts.get(artifact_name)
        artifact_entry = artifact_manifest_artifacts.get(artifact_name)
        if not isinstance(identity_entry, dict):
            errors.append(f"{label} artifacts.{artifact_name} must be an object")
            continue
        identity_sha = identity_entry.get("sha256")
        if not _is_sha256(identity_sha):
            errors.append(f"{label} artifacts.{artifact_name}.sha256 must be a SHA-256 digest")
            continue
        if not isinstance(artifact_entry, dict) or identity_sha.strip() != str(artifact_entry.get("sha256", "")).strip():
            errors.append(
                f"{label} artifacts.{artifact_name}.sha256 does not match artifact_manifest.json"
            )


def _coerce_selected_flag(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return (
        series.fillna(False)
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"1", "true", "yes", "y", "on"})
    )


def _selected_symbols_ordered_by_rank(
    latest_ranking: pd.DataFrame,
    selected_mask: pd.Series,
    errors: list[str],
) -> list[str]:
    selected_rows = latest_ranking.loc[selected_mask, ["symbol", "current_rank"]].copy()
    if selected_rows.empty:
        return []

    selected_rows["_symbol_normalized"] = selected_rows["symbol"].astype(str).str.strip().str.upper()
    selected_rows["_current_rank_numeric"] = pd.to_numeric(selected_rows["current_rank"], errors="coerce")

    invalid_rank_symbols = selected_rows.loc[
        selected_rows["_current_rank_numeric"].isna(),
        "_symbol_normalized",
    ].tolist()
    if invalid_rank_symbols:
        errors.append(
            "latest_ranking.csv selected rows must have numeric current_rank values: "
            + ", ".join(invalid_rank_symbols)
        )
        return []

    duplicated_rank_symbols = selected_rows.loc[
        selected_rows["_current_rank_numeric"].duplicated(keep=False),
        "_symbol_normalized",
    ].tolist()
    if duplicated_rank_symbols:
        errors.append(
            "latest_ranking.csv selected current_rank values must be unique: "
            + ", ".join(duplicated_rank_symbols)
        )

    ordered = selected_rows.sort_values("_current_rank_numeric", kind="mergesort")
    return ordered["_symbol_normalized"].tolist()


def validate_release_outputs(
    output_dir: Path | str,
    *,
    expected_mode: str | None = None,
    expected_source_project: str | None = None,
    expected_pool_size: int | None = None,
    reference_date: Any = None,
    max_age_days: int | None = None,
    require_manifest: bool = False,
    require_artifact_manifest: bool = False,
    require_runtime_evidence_identity: bool = False,
    require_freshness: bool = False,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    errors: list[str] = []
    warnings: list[str] = []
    loaded_files: dict[str, Any] = {}

    for filename in REQUIRED_OUTPUT_FILES:
        path = output_path / filename
        if not path.exists():
            errors.append(f"missing required output: {path}")
            continue
        loaded_files[filename] = path

    manifest_path = output_path / REQUIRED_MANIFEST_FILE
    manifest_present = manifest_path.exists()
    if require_manifest and not manifest_present:
        errors.append(f"missing required output: {manifest_path}")
    elif manifest_present:
        loaded_files[REQUIRED_MANIFEST_FILE] = manifest_path

    artifact_manifest_path = output_path / REQUIRED_ARTIFACT_MANIFEST_FILE
    artifact_manifest_present = artifact_manifest_path.exists()
    if require_artifact_manifest and not artifact_manifest_present:
        errors.append(f"missing required output: {artifact_manifest_path}")
    elif artifact_manifest_present:
        loaded_files[REQUIRED_ARTIFACT_MANIFEST_FILE] = artifact_manifest_path

    if errors:
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "output_dir": str(output_path),
            "manifest_present": manifest_present,
            "artifact_manifest_present": artifact_manifest_present,
        }

    latest_universe = read_json(loaded_files["latest_universe.json"], default={}) or {}
    live_pool = read_json(loaded_files["live_pool.json"], default={}) or {}
    live_pool_legacy = read_json(loaded_files["live_pool_legacy.json"], default={}) or {}
    manifest = read_json(manifest_path, default={}) or {}
    artifact_manifest = read_json(artifact_manifest_path, default={}) or {}

    ranking_path = loaded_files["latest_ranking.csv"]
    try:
        latest_ranking = pd.read_csv(ranking_path)
    except Exception as exc:
        errors.append(f"failed to read latest_ranking.csv: {exc}")
        latest_ranking = pd.DataFrame()

    if not isinstance(latest_universe, dict):
        errors.append("latest_universe.json must contain an object")
        latest_universe = {}
    if not isinstance(live_pool, dict):
        errors.append("live_pool.json must contain an object")
        live_pool = {}
    if not isinstance(live_pool_legacy, dict):
        errors.append("live_pool_legacy.json must contain an object")
        live_pool_legacy = {}
    if manifest_present and not isinstance(manifest, dict):
        errors.append("release_manifest.json must contain an object")
        manifest = {}
    if artifact_manifest_present and not isinstance(artifact_manifest, dict):
        errors.append("artifact_manifest.json must contain an object")
        artifact_manifest = {}

    _append_missing_fields(live_pool, REQUIRED_LIVE_POOL_FIELDS, errors, "live_pool.json")
    _append_missing_fields(live_pool_legacy, REQUIRED_LIVE_POOL_FIELDS, errors, "live_pool_legacy.json")

    universe_symbols = _normalize_symbol_list(
        latest_universe.get("symbols"),
        "latest_universe.json symbols",
        errors,
    )
    live_pool_symbols = _normalize_symbol_list(live_pool.get("symbols"), "live_pool.json symbols", errors)
    live_pool_symbol_map = _normalize_symbol_map(
        live_pool.get("symbol_map"),
        "live_pool.json symbol_map",
        errors,
    )
    legacy_symbols = _normalize_symbol_map(
        live_pool_legacy.get("symbols"),
        "live_pool_legacy.json symbols",
        errors,
    )
    legacy_symbol_map = _normalize_symbol_map(
        live_pool_legacy.get("symbol_map"),
        "live_pool_legacy.json symbol_map",
        errors,
    )

    live_pool_size = _normalize_pool_size(live_pool.get("pool_size"), "live_pool.json pool_size", errors)
    legacy_pool_size = _normalize_pool_size(
        live_pool_legacy.get("pool_size"),
        "live_pool_legacy.json pool_size",
        errors,
    )
    live_pool_mode = str(live_pool.get("mode", "")).strip()
    legacy_mode = str(live_pool_legacy.get("mode", "")).strip()
    if not live_pool_mode:
        errors.append("live_pool.json mode must be a non-empty string")
    if not legacy_mode:
        errors.append("live_pool_legacy.json mode must be a non-empty string")

    live_pool_source_project = _normalize_source_project(
        live_pool.get("source_project"),
        "live_pool.json source_project",
        errors,
    )
    legacy_source_project = _normalize_source_project(
        live_pool_legacy.get("source_project"),
        "live_pool_legacy.json source_project",
        errors,
    )

    universe_as_of_date, universe_as_of_ts = _parse_as_of_date(latest_universe.get("as_of_date"))
    live_pool_as_of_date, live_pool_as_of_ts = _parse_as_of_date(live_pool.get("as_of_date"))
    legacy_as_of_date, legacy_as_of_ts = _parse_as_of_date(live_pool_legacy.get("as_of_date"))
    if universe_as_of_ts is None:
        errors.append("latest_universe.json as_of_date is missing or invalid")
    if live_pool_as_of_ts is None:
        errors.append("live_pool.json as_of_date is missing or invalid")
    if legacy_as_of_ts is None:
        errors.append("live_pool_legacy.json as_of_date is missing or invalid")

    live_pool_version = str(live_pool.get("version", "")).strip()
    legacy_version = str(live_pool_legacy.get("version", "")).strip()
    if not live_pool_version:
        errors.append("live_pool.json version must be a non-empty string")
    if not legacy_version:
        errors.append("live_pool_legacy.json version must be a non-empty string")

    if live_pool_symbols and live_pool_symbol_map and set(live_pool_symbols) != set(live_pool_symbol_map):
        errors.append("live_pool.json symbols and symbol_map keys do not match")
    if legacy_symbols and legacy_symbol_map and legacy_symbols != legacy_symbol_map:
        errors.append("live_pool_legacy.json symbols and symbol_map must be identical mappings")
    if live_pool_symbols and legacy_symbols and set(live_pool_symbols) != set(legacy_symbols):
        errors.append("live_pool.json symbols do not match live_pool_legacy.json symbols")
    if live_pool_size is not None and live_pool_symbols and live_pool_size != len(live_pool_symbols):
        errors.append(
            f"live_pool.json pool_size mismatch: declared {live_pool_size} vs parsed {len(live_pool_symbols)}"
        )
    if legacy_pool_size is not None and legacy_symbols and legacy_pool_size != len(legacy_symbols):
        errors.append(
            f"live_pool_legacy.json pool_size mismatch: declared {legacy_pool_size} vs parsed {len(legacy_symbols)}"
        )
    if universe_symbols and live_pool_symbols and not set(live_pool_symbols).issubset(set(universe_symbols)):
        errors.append("live_pool.json symbols must be a subset of latest_universe.json symbols")

    if live_pool_as_of_date and live_pool_mode:
        expected_version = build_release_version(live_pool_as_of_date, live_pool_mode)
        if live_pool_version and live_pool_version != expected_version:
            errors.append(f"live_pool.json version mismatch: expected {expected_version} got {live_pool_version}")
    if legacy_as_of_date and legacy_mode:
        expected_legacy_version = build_release_version(legacy_as_of_date, legacy_mode)
        if legacy_version and legacy_version != expected_legacy_version:
            errors.append(
                f"live_pool_legacy.json version mismatch: expected {expected_legacy_version} got {legacy_version}"
            )

    as_of_values = {value for value in (universe_as_of_date, live_pool_as_of_date, legacy_as_of_date) if value}
    if not latest_ranking.empty and "as_of_date" in latest_ranking.columns:
        as_of_values.update(str(value).strip() for value in latest_ranking["as_of_date"].dropna().unique())
    if manifest_present:
        manifest_as_of_date, _ = _parse_as_of_date(manifest.get("as_of_date"))
        if manifest_as_of_date:
            as_of_values.add(manifest_as_of_date)
    if artifact_manifest_present:
        artifact_manifest_as_of_date, _ = _parse_as_of_date(artifact_manifest.get("as_of_date"))
        if artifact_manifest_as_of_date:
            as_of_values.add(artifact_manifest_as_of_date)
    if len(as_of_values) > 1:
        errors.append(f"release outputs have inconsistent as_of_date values: {sorted(as_of_values)}")

    if live_pool_mode and legacy_mode and live_pool_mode != legacy_mode:
        errors.append("live_pool.json mode does not match live_pool_legacy.json mode")
    if live_pool_version and legacy_version and live_pool_version != legacy_version:
        errors.append("live_pool.json version does not match live_pool_legacy.json version")
    if live_pool_source_project and legacy_source_project and live_pool_source_project != legacy_source_project:
        errors.append("live_pool.json source_project does not match live_pool_legacy.json source_project")

    if expected_mode and live_pool_mode and live_pool_mode != str(expected_mode):
        errors.append(f"live_pool.json mode mismatch: expected {expected_mode} got {live_pool_mode}")
    if expected_source_project and live_pool_source_project and live_pool_source_project != str(expected_source_project):
        errors.append(
            f"live_pool.json source_project mismatch: expected {expected_source_project} got {live_pool_source_project}"
        )
    if expected_pool_size is not None and live_pool_size is not None and live_pool_size != int(expected_pool_size):
        errors.append(
            f"live_pool.json pool_size mismatch: expected {int(expected_pool_size)} got {live_pool_size}"
        )

    missing_ranking_columns = [column for column in REQUIRED_RANKING_COLUMNS if column not in latest_ranking.columns]
    if missing_ranking_columns:
        errors.append(f"latest_ranking.csv missing columns: {missing_ranking_columns}")
    elif latest_ranking.empty:
        errors.append("latest_ranking.csv must contain at least one row")
    else:
        ranking_symbols = _normalize_symbol_list(
            latest_ranking["symbol"].tolist(),
            "latest_ranking.csv symbol",
            errors,
        )
        ranking_as_of_values = sorted(str(value).strip() for value in latest_ranking["as_of_date"].dropna().unique())
        if len(ranking_as_of_values) != 1:
            errors.append(f"latest_ranking.csv must contain exactly one as_of_date, got {ranking_as_of_values}")
        selected_mask = _coerce_selected_flag(latest_ranking["selected_flag"])
        selected_symbols = set(
            _normalize_symbol_list(
                latest_ranking.loc[selected_mask, "symbol"].tolist(),
                "latest_ranking.csv selected_flag symbols",
                errors,
            )
        )
        selected_row_count = int(selected_mask.sum())
        selected_symbols_by_rank = _selected_symbols_ordered_by_rank(latest_ranking, selected_mask, errors)
        if live_pool_symbols and not set(live_pool_symbols).issubset(set(ranking_symbols)):
            errors.append("live_pool.json symbols must all be present in latest_ranking.csv")
        if live_pool_symbols and not set(live_pool_symbols).issubset(selected_symbols):
            errors.append("live_pool.json symbols must all be selected in latest_ranking.csv")
        if live_pool_symbols and selected_row_count != len(live_pool_symbols):
            errors.append(
                "latest_ranking.csv selected_flag row count must match live_pool.json symbols length"
            )
        if live_pool_symbols and selected_symbols_by_rank:
            expected_live_pool_symbols = selected_symbols_by_rank[: len(live_pool_symbols)]
            if live_pool_symbols != expected_live_pool_symbols:
                errors.append(
                    "live_pool.json symbols must match selected latest_ranking.csv symbols ordered by current_rank"
                )

    if manifest_present:
        manifest_mode = str(manifest.get("mode", "")).strip()
        manifest_version = str(manifest.get("version", "")).strip()
        if manifest_mode and live_pool_mode and manifest_mode != live_pool_mode:
            errors.append("release_manifest.json mode does not match live_pool.json mode")
        if manifest_version and live_pool_version and manifest_version != live_pool_version:
            errors.append("release_manifest.json version does not match live_pool.json version")
        manifest_as_of_date, _ = _parse_as_of_date(manifest.get("as_of_date"))
        if manifest_as_of_date and live_pool_as_of_date and manifest_as_of_date != live_pool_as_of_date:
            errors.append("release_manifest.json as_of_date does not match live_pool.json as_of_date")

        firestore_section = manifest.get("firestore", {})
        if not isinstance(firestore_section, dict):
            errors.append("release_manifest.json firestore must be an object")
            firestore_payload = {}
        else:
            firestore_payload = firestore_section.get("payload", {})
        if not isinstance(firestore_payload, dict):
            errors.append("release_manifest.json firestore.payload must be an object")
        else:
            firestore_symbols = firestore_payload.get("symbols")
            if firestore_symbols != live_pool_symbols:
                errors.append("release_manifest.json firestore.payload symbols do not match live_pool.json symbols")
            if firestore_payload.get("symbol_map") != legacy_symbol_map:
                errors.append(
                    "release_manifest.json firestore.payload symbol_map does not match live_pool_legacy.json symbol_map"
                )
            if str(firestore_payload.get("version", "")).strip() != live_pool_version:
                errors.append("release_manifest.json firestore.payload version does not match live_pool.json version")
            if str(firestore_payload.get("mode", "")).strip() != live_pool_mode:
                errors.append("release_manifest.json firestore.payload mode does not match live_pool.json mode")
            if str(firestore_payload.get("as_of_date", "")).strip() != live_pool_as_of_date:
                errors.append(
                    "release_manifest.json firestore.payload as_of_date does not match live_pool.json as_of_date"
                )
            try:
                firestore_pool_size = int(firestore_payload.get("pool_size", 0))
            except Exception:
                firestore_pool_size = -1
            if firestore_pool_size != len(live_pool_symbols):
                errors.append("release_manifest.json firestore.payload pool_size does not match live_pool.json")
            if str(firestore_payload.get("source_project", "")).strip() != live_pool_source_project:
                errors.append(
                    "release_manifest.json firestore.payload source_project does not match live_pool.json"
                )

    if artifact_manifest_present:
        _append_missing_fields(
            artifact_manifest,
            REQUIRED_ARTIFACT_MANIFEST_FIELDS,
            errors,
            "artifact_manifest.json",
        )
        if str(artifact_manifest.get("manifest_type", "")).strip() != "strategy_artifact":
            errors.append("artifact_manifest.json manifest_type must be strategy_artifact")
        if str(artifact_manifest.get("contract_version", "")).strip() != EXPECTED_ARTIFACT_CONTRACT_VERSION:
            errors.append(
                f"artifact_manifest.json contract_version must be {EXPECTED_ARTIFACT_CONTRACT_VERSION}"
            )
        if str(artifact_manifest.get("strategy_profile", "")).strip() != "crypto_live_pool_rotation":
            errors.append("artifact_manifest.json strategy_profile must be crypto_live_pool_rotation")
        if str(artifact_manifest.get("artifact_type", "")).strip() != "live_pool":
            errors.append("artifact_manifest.json artifact_type must be live_pool")
        if str(artifact_manifest.get("primary_artifact", "")).strip() != "live_pool":
            errors.append("artifact_manifest.json primary_artifact must be live_pool")
        if str(artifact_manifest.get("version", "")).strip() != live_pool_version:
            errors.append("artifact_manifest.json version does not match live_pool.json version")
        if str(artifact_manifest.get("mode", "")).strip() != live_pool_mode:
            errors.append("artifact_manifest.json mode does not match live_pool.json mode")
        artifact_as_of_date, _ = _parse_as_of_date(artifact_manifest.get("as_of_date"))
        if artifact_as_of_date and live_pool_as_of_date and artifact_as_of_date != live_pool_as_of_date:
            errors.append("artifact_manifest.json as_of_date does not match live_pool.json as_of_date")
        artifact_snapshot_as_of, _ = _parse_as_of_date(artifact_manifest.get("snapshot_as_of"))
        if artifact_snapshot_as_of and live_pool_as_of_date and artifact_snapshot_as_of != live_pool_as_of_date:
            errors.append("artifact_manifest.json snapshot_as_of does not match live_pool.json as_of_date")
        if str(artifact_manifest.get("source_project", "")).strip() != live_pool_source_project:
            errors.append("artifact_manifest.json source_project does not match live_pool.json")
        artifact_symbols = artifact_manifest.get("symbols")
        if artifact_symbols != live_pool_symbols:
            errors.append("artifact_manifest.json symbols do not match live_pool.json symbols")
        try:
            artifact_symbol_count = int(artifact_manifest.get("symbol_count", -1))
        except Exception:
            artifact_symbol_count = -1
        if artifact_symbol_count != len(live_pool_symbols):
            errors.append("artifact_manifest.json symbol_count does not match live_pool.json")
        artifacts = artifact_manifest.get("artifacts", {})
        if not isinstance(artifacts, dict):
            errors.append("artifact_manifest.json artifacts must be an object")
            artifacts = {}
        for artifact_name, expected_filename in REQUIRED_ARTIFACT_MANIFEST_ARTIFACTS.items():
            artifact_entry = artifacts.get(artifact_name)
            if not isinstance(artifact_entry, dict):
                errors.append(f"artifact_manifest.json artifacts.{artifact_name} must be an object")
                continue
            artifact_path = str(artifact_entry.get("path", "")).strip()
            if not artifact_path:
                errors.append(f"artifact_manifest.json artifacts.{artifact_name}.path must be non-empty")
                continue
            if Path(artifact_path).name != expected_filename:
                errors.append(
                    f"artifact_manifest.json artifacts.{artifact_name}.path must point to {expected_filename}"
                )
                continue
            resolved_path = output_path / artifact_path
            if not resolved_path.exists():
                errors.append(f"artifact_manifest.json artifacts.{artifact_name}.path does not exist: {artifact_path}")
                continue
            expected_sha = str(artifact_entry.get("sha256", "")).strip()
            if not expected_sha:
                errors.append(f"artifact_manifest.json artifacts.{artifact_name}.sha256 must be non-empty")
            elif expected_sha != _sha256_file(resolved_path):
                errors.append(f"artifact_manifest.json artifacts.{artifact_name}.sha256 does not match file content")

    if require_runtime_evidence_identity and manifest_present and artifact_manifest_present:
        _validate_runtime_evidence_identity(
            manifest,
            artifact_manifest,
            live_pool_mode=live_pool_mode,
            live_pool_version=live_pool_version,
            errors=errors,
        )

    age_days: int | None = None
    if live_pool_as_of_ts is not None:
        if reference_date is None:
            reference_ts = pd.Timestamp.now(tz="UTC").normalize()
        else:
            reference_ts = pd.Timestamp(reference_date).normalize()
        age_days = (reference_ts.date() - live_pool_as_of_ts.date()).days
        if age_days < 0:
            errors.append(f"as_of_date {live_pool_as_of_date} is in the future")
        elif max_age_days is not None and age_days > int(max_age_days):
            if require_freshness:
                errors.append(
                    f"release outputs are stale by {age_days} days (max {int(max_age_days)}): {live_pool_as_of_date}"
                )
            else:
                warnings.append(
                    f"release outputs are older than {int(max_age_days)} days ({age_days} days): {live_pool_as_of_date}"
                )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "output_dir": str(output_path),
        "manifest_present": manifest_present,
        "artifact_manifest_present": artifact_manifest_present,
        "artifact_contract_version": str(artifact_manifest.get("contract_version", "")).strip()
        if artifact_manifest_present
        else None,
        "as_of_date": live_pool_as_of_date,
        "version": live_pool_version,
        "mode": live_pool_mode,
        "pool_size": len(live_pool_symbols),
        "symbols": live_pool_symbols,
        "source_project": live_pool_source_project,
        "age_days": age_days,
    }


def assert_release_outputs(
    output_dir: Path | str,
    **kwargs: Any,
) -> dict[str, Any]:
    validation = validate_release_outputs(output_dir, **kwargs)
    if validation["ok"]:
        return validation

    issues = [f"- {message}" for message in validation["errors"]]
    if validation["warnings"]:
        issues.append("Warnings:")
        issues.extend(f"- {message}" for message in validation["warnings"])
    raise ValueError("Release contract validation failed:\n" + "\n".join(issues))
