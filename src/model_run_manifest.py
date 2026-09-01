from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


MODEL_RUN_MANIFEST_CONTRACT_VERSION = "model_run_manifest.v1"
BACKEND_ALLOWLIST = {
    "linear": {"sklearn_ridge", "sklearn_elasticnet", "numpy_ridge"},
    "ml": {"lightgbm", "hist_gradient_boosting", "random_forest", "numpy_ridge"},
}
VERSION_PATTERN = re.compile(r"\d+(?:\.\d+){0,3}(?:[A-Za-z][A-Za-z0-9.+-]*)?$")


class ModelRunManifestError(ValueError):
    """Raised when model-run evidence cannot be represented safely."""


@dataclass(frozen=True)
class DependencyLock:
    path: str
    content: bytes
    sha256: str
    pins: dict[str, str]


def _normalized_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _dependency_lock_from_bytes(path: str, content: bytes) -> DependencyLock:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ModelRunManifestError("dependency lock file must be UTF-8.") from exc
    pins: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        package_match = re.match(r"^([A-Za-z0-9_.-]+)", line)
        package = _normalized_package_name(package_match.group(1)) if package_match else ""
        if package in {"numpy", "scikit-learn", "lightgbm"}:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.!+]+)", line)
            if match is None:
                raise ModelRunManifestError("dependency lock contains an ambiguous backend pin.")
            normalized_package = _normalized_package_name(match.group(1))
            if normalized_package in pins:
                raise ModelRunManifestError("dependency lock contains a duplicate backend pin.")
            pins[normalized_package] = match.group(2)
    return DependencyLock(
        path=path,
        content=content,
        sha256=_sha256_bytes(content),
        pins=pins,
    )


def read_dependency_lock(path: Path | str) -> DependencyLock:
    lock_path = Path(path)
    try:
        content = lock_path.read_bytes()
    except OSError as exc:
        raise ModelRunManifestError("dependency lock file is required.") from exc
    return _dependency_lock_from_bytes(lock_path.name, content)


def _validated_dependency_lock(snapshot: DependencyLock) -> DependencyLock:
    if not isinstance(snapshot, DependencyLock):
        raise ModelRunManifestError("dependency_lock must be a single-read lock snapshot.")
    parsed = _dependency_lock_from_bytes(snapshot.path, snapshot.content)
    if snapshot.sha256 != parsed.sha256 or snapshot.pins != parsed.pins:
        raise ModelRunManifestError("dependency_lock snapshot does not match its bytes.")
    return parsed


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == "unknown":
        raise ModelRunManifestError(f"{label} must not be empty or unknown.")
    return value.strip()


def _canonical_value(value: Any, *, label: str, allow_missing: bool) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _canonical_value(asdict(value), label=label, allow_missing=allow_missing)
    if isinstance(value, np.generic):
        return _canonical_value(value.item(), label=label, allow_missing=allow_missing)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            if allow_missing:
                return None
            raise ModelRunManifestError(f"{label} must be finite.")
        return value.isoformat()
    if value is None:
        if allow_missing:
            return None
        raise ModelRunManifestError(f"{label} is required.")
    if isinstance(value, float):
        if not math.isfinite(value):
            if allow_missing and math.isnan(value):
                return None
            raise ModelRunManifestError(f"{label} must be finite.")
        return value
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise ModelRunManifestError(f"{label} keys must be strings.")
            normalized[key] = _canonical_value(value[key], label=f"{label}.{key}", allow_missing=allow_missing)
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(item, label=f"{label}[{index}]", allow_missing=allow_missing)
            for index, item in enumerate(value)
        ]
    if pd.isna(value):
        if allow_missing:
            return None
        raise ModelRunManifestError(f"{label} must be finite.")
    raise ModelRunManifestError(f"{label} has unsupported value type: {type(value).__name__}.")


def _canonical_json_bytes(payload: Any, *, allow_missing: bool = False) -> bytes:
    normalized = _canonical_value(payload, label="manifest", allow_missing=allow_missing)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_digest(
    frame: pd.DataFrame,
    *,
    columns: list[str],
    label: str,
    allow_missing: bool,
) -> str:
    if not isinstance(frame.index, pd.MultiIndex) or list(frame.index.names) != ["date", "symbol"]:
        raise ModelRunManifestError(f"{label} must use a (date, symbol) index.")
    required_columns = [*columns]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ModelRunManifestError(f"{label} missing required columns: {missing}.")
    materialized = frame.loc[:, required_columns].reset_index().sort_values(
        ["date", "symbol"], kind="mergesort"
    )
    records = [
        {
            str(column): _canonical_value(value, label=f"{label}.{column}", allow_missing=allow_missing)
            for column, value in row.items()
        }
        for row in materialized.to_dict(orient="records")
    ]
    return _sha256_bytes(_canonical_json_bytes(records, allow_missing=allow_missing))


def _training_window(train_df: pd.DataFrame) -> dict[str, Any]:
    if train_df.empty:
        raise ModelRunManifestError("training data must not be empty.")
    if not isinstance(train_df.index, pd.MultiIndex) or list(train_df.index.names) != ["date", "symbol"]:
        raise ModelRunManifestError("training data must use a (date, symbol) index.")
    dates = pd.to_datetime(train_df.index.get_level_values("date"), errors="coerce")
    if dates.isna().any():
        raise ModelRunManifestError("training window contains an invalid date.")
    return {
        "start": pd.Timestamp(dates.min()).normalize().strftime("%Y-%m-%d"),
        "end": pd.Timestamp(dates.max()).normalize().strftime("%Y-%m-%d"),
        "rows": int(len(train_df)),
    }


def _validated_backends(backends: Any) -> dict[str, dict[str, str]]:
    if not isinstance(backends, Mapping) or set(backends) != {"linear", "ml"}:
        raise ModelRunManifestError("backends must contain exactly linear and ml entries.")
    normalized: dict[str, dict[str, str]] = {}
    for role in ("linear", "ml"):
        backend = backends[role]
        if not isinstance(backend, Mapping):
            raise ModelRunManifestError(f"backends.{role} must be an object.")
        name = _required_text(backend.get("name"), f"backends.{role}.name")
        if name not in BACKEND_ALLOWLIST[role]:
            raise ModelRunManifestError(f"{role} backend is not allowed: {name}.")
        version = _required_text(backend.get("version"), f"backends.{role}.version")
        if not VERSION_PATTERN.fullmatch(version):
            raise ModelRunManifestError(f"backends.{role}.version is invalid.")
        normalized[role] = {
            "name": name,
            "version": version,
        }
    return normalized


def build_model_run_manifest(
    *,
    model_family: Any,
    backends: Any,
    feature_columns: list[str],
    label_column: Any,
    train_df: pd.DataFrame,
    predictions: pd.DataFrame,
    config: Mapping[str, Any],
    source_revision: Any,
    seed: Any,
    dependency_lock: DependencyLock,
) -> dict[str, Any]:
    """Build a canonical, fail-closed record of one trained model run."""
    model_family_text = _required_text(model_family, "model_family")
    label_column_text = _required_text(label_column, "label_column")
    if not isinstance(feature_columns, list) or not feature_columns:
        raise ModelRunManifestError("feature_columns must be a non-empty list.")
    normalized_features = [_required_text(column, "feature_columns") for column in feature_columns]
    if len(set(normalized_features)) != len(normalized_features):
        raise ModelRunManifestError("feature_columns must not contain duplicates.")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ModelRunManifestError("seed must be a finite integer.")
    source_revision_text = _required_text(source_revision, "source_revision")
    if not re.fullmatch(r"[0-9a-f]{40}", source_revision_text):
        raise ModelRunManifestError("source_revision must be a 40-character lowercase git SHA.")
    dependency_lock = _validated_dependency_lock(dependency_lock)
    if label_column_text not in train_df.columns:
        raise ModelRunManifestError(f"training data missing required label column: {label_column_text}.")

    normalized_backends = _validated_backends(backends)
    config_projection = _config_projection(config, seed)
    config_digest = _sha256_bytes(_canonical_json_bytes(config_projection, allow_missing=True))
    training_columns = [*normalized_features, label_column_text]
    prediction_columns = ["linear_score_raw", "ml_score_raw"]
    manifest = {
        "contract_version": MODEL_RUN_MANIFEST_CONTRACT_VERSION,
        "model_family": model_family_text,
        "backends": normalized_backends,
        "dependency_lock": {"path": dependency_lock.path, "sha256": dependency_lock.sha256},
        "feature_schema": {"columns": normalized_features},
        "label_schema": {"column": label_column_text},
        "training_window": _training_window(train_df),
        "training_data": {
            "sha256": _frame_digest(
                train_df,
                columns=training_columns,
                label="training_data",
                allow_missing=True,
            )
        },
        "config": {"sha256": config_digest},
        "config_projection": config_projection,
        "source": {
            "revision": source_revision_text,
            "sha256": _sha256_bytes(source_revision_text.encode("utf-8")),
        },
        "seed": seed,
        "prediction_artifact": {
            "format": "dataframe_records.v1",
            "sha256": _frame_digest(
                predictions,
                columns=prediction_columns,
                label="prediction_artifact",
                allow_missing=False,
            ),
        },
    }
    validate_model_run_manifest(manifest)
    return manifest


def _config_projection(config: Mapping[str, Any], seed: int) -> dict[str, Any]:
    section_names = {
        "data": "data",
        "universe": "universe",
        "features": "feature_engineering",
        "labels": "labels",
        "walkforward": "walkforward",
        "model": "model",
    }
    projection: dict[str, Any] = {"seed": seed}
    for output_name, config_name in section_names.items():
        value = config.get(config_name)
        if not isinstance(value, Mapping):
            raise ModelRunManifestError(f"config.{config_name} must be an object for model evidence.")
        projection[output_name] = dict(value)
    return projection


def validate_model_run_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise ModelRunManifestError("model_run_manifest must be an object.")
    required_fields = {
        "contract_version",
        "model_family",
        "backends",
        "dependency_lock",
        "feature_schema",
        "label_schema",
        "training_window",
        "training_data",
        "config",
        "config_projection",
        "source",
        "seed",
        "prediction_artifact",
    }
    missing = sorted(required_fields - set(manifest))
    if missing:
        raise ModelRunManifestError(f"model_run_manifest missing required fields: {missing}.")
    unknown = sorted(set(manifest) - required_fields)
    if unknown:
        raise ModelRunManifestError(f"model_run_manifest contains unknown fields: {unknown}.")
    if manifest.get("contract_version") != MODEL_RUN_MANIFEST_CONTRACT_VERSION:
        raise ModelRunManifestError("model_run_manifest has an unsupported contract version.")
    _required_text(manifest.get("model_family"), "model_family")
    for role, backend in _validated_backends(manifest.get("backends")).items():
        if set(manifest["backends"][role]) != {"name", "version"}:
            raise ModelRunManifestError(f"backends.{role} contains unknown fields.")
        _required_text(backend["name"], f"backends.{role}.name")
    for field in ("dependency_lock", "training_data", "config", "source", "prediction_artifact"):
        if not isinstance(manifest.get(field), Mapping):
            raise ModelRunManifestError(f"{field} must be an object.")
    if set(manifest["dependency_lock"]) != {"path", "sha256"}:
        raise ModelRunManifestError("dependency_lock contains unknown fields.")
    if manifest["dependency_lock"].get("path") != "requirements-lock.txt":
        raise ModelRunManifestError("dependency_lock.path must be requirements-lock.txt.")
    for field in ("dependency_lock", "training_data", "config", "prediction_artifact"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest[field].get("sha256", ""))):
            raise ModelRunManifestError(f"{field}.sha256 must be a SHA-256 digest.")
    source = manifest["source"]
    if not re.fullmatch(r"[0-9a-f]{40}", str(source.get("revision", ""))):
        raise ModelRunManifestError("source.revision must be a 40-character lowercase git SHA.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", ""))):
        raise ModelRunManifestError("source.sha256 must be a SHA-256 digest.")
    if source["sha256"] != _sha256_bytes(str(source["revision"]).encode("utf-8")):
        raise ModelRunManifestError("source.sha256 must equal the source revision digest.")
    feature_schema = manifest.get("feature_schema")
    if not isinstance(feature_schema, Mapping) or set(feature_schema) != {"columns"} or not isinstance(feature_schema.get("columns"), list):
        raise ModelRunManifestError("feature_schema.columns must be a list.")
    if not feature_schema["columns"]:
        raise ModelRunManifestError("feature_schema.columns must not be empty.")
    if any(not isinstance(column, str) or not column.strip() for column in feature_schema["columns"]):
        raise ModelRunManifestError("feature_schema.columns must contain non-empty strings.")
    if len(set(feature_schema["columns"])) != len(feature_schema["columns"]):
        raise ModelRunManifestError("feature_schema.columns must not contain duplicates.")
    label_schema = manifest.get("label_schema")
    if not isinstance(label_schema, Mapping) or set(label_schema) != {"column"}:
        raise ModelRunManifestError("label_schema must contain only column.")
    _required_text(label_schema.get("column"), "label_schema.column")
    training_window = manifest.get("training_window")
    if not isinstance(training_window, Mapping) or set(training_window) != {"start", "end", "rows"}:
        raise ModelRunManifestError("training_window contains unknown fields.")
    for field in ("start", "end"):
        if not isinstance(training_window.get(field), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", training_window[field]):
            raise ModelRunManifestError(f"training_window.{field} must be an ISO date.")
    if training_window["start"] > training_window["end"]:
        raise ModelRunManifestError("training_window.start must not be after training_window.end.")
    if not isinstance(training_window.get("rows"), int) or isinstance(training_window["rows"], bool) or training_window["rows"] <= 0:
        raise ModelRunManifestError("training_window.rows must be a positive integer.")
    if set(manifest["training_data"]) != {"sha256"} or set(manifest["config"]) != {"sha256"}:
        raise ModelRunManifestError("training_data and config must contain only sha256.")
    prediction_artifact = manifest["prediction_artifact"]
    if set(prediction_artifact) != {"format", "sha256"} or prediction_artifact.get("format") != "dataframe_records.v1":
        raise ModelRunManifestError("prediction_artifact format is invalid.")
    projection = manifest.get("config_projection")
    if not isinstance(projection, Mapping) or set(projection) != {"data", "universe", "features", "labels", "walkforward", "model", "seed"}:
        raise ModelRunManifestError("config_projection contains unknown or missing fields.")
    if projection.get("seed") != manifest.get("seed"):
        raise ModelRunManifestError("config_projection.seed must equal seed.")
    for field in ("data", "universe", "features", "labels", "walkforward", "model"):
        if not isinstance(projection.get(field), Mapping):
            raise ModelRunManifestError(f"config_projection.{field} must be an object.")
    expected_config_digest = _sha256_bytes(_canonical_json_bytes(projection, allow_missing=True))
    if manifest["config"].get("sha256") != expected_config_digest:
        raise ModelRunManifestError("config.sha256 must equal the canonical config_projection digest.")
    if not isinstance(manifest.get("seed"), int) or isinstance(manifest.get("seed"), bool):
        raise ModelRunManifestError("seed must be a finite integer.")
    _canonical_json_bytes(dict(manifest), allow_missing=True)
    return dict(manifest)


def canonical_model_run_manifest_bytes(manifest: Any) -> bytes:
    return _canonical_json_bytes(validate_model_run_manifest(manifest), allow_missing=True)


def canonical_model_run_manifest_digest(manifest: Any) -> str:
    return _sha256_bytes(canonical_model_run_manifest_bytes(manifest))


def write_model_run_manifest(path: Path | str, manifest: Any) -> str:
    payload = canonical_model_run_manifest_bytes(manifest)
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(payload)
    return _sha256_bytes(payload)
