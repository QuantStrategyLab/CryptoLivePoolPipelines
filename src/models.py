from __future__ import annotations

import logging
from importlib import metadata
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from .export import resolve_clean_source_revision
from .model_run_manifest import DependencyLock, build_model_run_manifest, read_dependency_lock

_logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import lightgbm as lgb
except (ModuleNotFoundError, OSError):  # pragma: no cover - optional dependency
    lgb = None
    _logger.info("lightgbm not available")

try:  # pragma: no cover - optional dependency
    from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import ElasticNet, Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
except (ModuleNotFoundError, ImportError):  # pragma: no cover - optional dependency
    HistGradientBoostingRegressor = None
    RandomForestRegressor = None
    ElasticNet = None
    Ridge = None
    Pipeline = None
    StandardScaler = None
    _logger.info("sklearn not available")


class ModelBackendConfigurationError(ValueError):
    """Raised when a model run omits or contradicts its backend contract."""


class ModelBackendUnavailableError(RuntimeError):
    """Raised when the declared backend cannot be loaded exactly."""


DEPENDENCY_LOCK_PATH = Path(__file__).resolve().parents[1] / "requirements-lock.txt"


@dataclass
class ModelPredictionResult:
    predictions: pd.DataFrame
    linear_backend: str
    ml_backend: str
    train_rows: int
    test_rows: int
    model_run_manifest: dict[str, Any] | None = None


class NumpyRidgeRegressor:
    """Simple ridge implementation used when sklearn is unavailable."""

    def __init__(self, alpha: float = 1.0) -> None:
        self.alpha = alpha
        self.coefficients_: Optional[np.ndarray] = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> "NumpyRidgeRegressor":
        x_bias = np.column_stack([np.ones(len(x)), x])
        identity = np.eye(x_bias.shape[1])
        identity[0, 0] = 0.0
        self.coefficients_ = np.linalg.pinv(x_bias.T @ x_bias + self.alpha * identity) @ x_bias.T @ y
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if self.coefficients_ is None:
            raise RuntimeError("The ridge regressor must be fit before predict is called.")
        x_bias = np.column_stack([np.ones(len(x)), x])
        return x_bias @ self.coefficients_


def _model_config(config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
    model_cfg = config.get("model")
    if not isinstance(model_cfg, dict):
        raise ModelBackendConfigurationError("model configuration must be an object.")
    execution_mode = str(model_cfg.get("execution_mode", "research")).strip().lower()
    if execution_mode not in {"production", "candidate", "research", "offline"}:
        raise ModelBackendConfigurationError("model.execution_mode is invalid.")
    fallback_value = model_cfg.get("compatibility_fallback_enabled", False)
    if not isinstance(fallback_value, bool):
        raise ModelBackendConfigurationError("model.compatibility_fallback_enabled must be boolean.")
    fallback_enabled = fallback_value
    if fallback_enabled and execution_mode not in {"research", "offline"}:
        raise ModelBackendConfigurationError(
            "model compatibility fallback is only allowed for research or offline runs."
        )
    return model_cfg, execution_mode, fallback_enabled


def _backend_candidates(model_cfg: dict[str, Any], role: str, fallback_enabled: bool) -> list[str]:
    key = f"{role}_backend"
    declared = model_cfg.get(key)
    if not isinstance(declared, str) or not declared.strip() or declared.strip().lower() == "unknown":
        raise ModelBackendConfigurationError(f"model.{key} must explicitly declare a known backend.")
    candidates = [declared.strip().lower()]
    if not fallback_enabled:
        return candidates
    fallbacks = model_cfg.get("compatibility_fallback_backends")
    if fallbacks is None:
        return candidates
    if not isinstance(fallbacks, dict):
        raise ModelBackendConfigurationError(
            "model.compatibility_fallback_backends must be an object."
        )
    role_fallbacks = fallbacks.get(role)
    if role_fallbacks is None:
        return candidates
    if not isinstance(role_fallbacks, list):
        raise ModelBackendConfigurationError(
            f"model.compatibility_fallback_backends.{role} must explicitly list fallback backends."
        )
    for backend in role_fallbacks:
        if not isinstance(backend, str) or not backend.strip() or backend.strip().lower() == "unknown":
            raise ModelBackendConfigurationError(f"model compatibility fallback {role} backend is invalid.")
        normalized = backend.strip().lower()
        if normalized not in candidates:
            candidates.append(normalized)
    return candidates


def _seed(model_cfg: dict[str, Any]) -> int:
    seed = model_cfg.get("random_state")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ModelBackendConfigurationError("model.random_state must be an integer.")
    return seed


def _model_family(model_cfg: dict[str, Any]) -> str:
    model_family = model_cfg.get("model_family")
    if not isinstance(model_family, str) or not model_family.strip() or model_family.strip().lower() == "unknown":
        raise ModelBackendConfigurationError("model.model_family must explicitly declare a known model family.")
    return model_family.strip()


def _build_linear_backend(backend: str, model_cfg: dict[str, Any], seed: int) -> Any:
    ridge_alpha = float(model_cfg.get("ridge_alpha", 1.0))
    if backend == "sklearn_ridge":
        if Pipeline is None or StandardScaler is None or Ridge is None:
            raise ModelBackendUnavailableError("Declared sklearn_ridge backend is unavailable.")
        if str(model_cfg.get("linear_model", "ridge")).lower() != "ridge":
            raise ModelBackendConfigurationError("model.linear_model does not match declared sklearn_ridge backend.")
        return Pipeline([("scaler", StandardScaler()), ("model", Ridge(alpha=ridge_alpha))])
    if backend == "sklearn_elasticnet":
        if Pipeline is None or StandardScaler is None or ElasticNet is None:
            raise ModelBackendUnavailableError("Declared sklearn_elasticnet backend is unavailable.")
        if str(model_cfg.get("linear_model", "ridge")).lower() != "elasticnet":
            raise ModelBackendConfigurationError("model.linear_model does not match declared sklearn_elasticnet backend.")
        return Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "model",
                    ElasticNet(
                        alpha=float(model_cfg.get("elasticnet_alpha", 0.01)),
                        l1_ratio=float(model_cfg.get("elasticnet_l1_ratio", 0.5)),
                        random_state=seed,
                        max_iter=5000,
                    ),
                ),
            ]
        )
    if backend == "numpy_ridge":
        return NumpyRidgeRegressor(alpha=ridge_alpha)
    raise ModelBackendConfigurationError(f"Declared linear backend is unknown: {backend}.")


def _build_tree_backend(backend: str, model_cfg: dict[str, Any], seed: int) -> Any:
    ridge_alpha = float(model_cfg.get("ridge_alpha", 1.0))
    if backend == "lightgbm":
        if lgb is None:
            raise ModelBackendUnavailableError("Declared lightgbm backend is unavailable.")
        params = dict(model_cfg.get("lightgbm_params", {}))
        params.setdefault("random_state", seed)
        return lgb.LGBMRegressor(**params)
    if backend == "hist_gradient_boosting":
        if HistGradientBoostingRegressor is None:
            raise ModelBackendUnavailableError("Declared hist_gradient_boosting backend is unavailable.")
        params = dict(model_cfg.get("hist_gbm_params", {}))
        params.setdefault("random_state", seed)
        return HistGradientBoostingRegressor(**params)
    if backend == "random_forest":
        if RandomForestRegressor is None:
            raise ModelBackendUnavailableError("Declared random_forest backend is unavailable.")
        params = dict(model_cfg.get("random_forest_params", {}))
        params.setdefault("random_state", seed)
        return RandomForestRegressor(**params)
    if backend == "numpy_ridge":
        return NumpyRidgeRegressor(alpha=ridge_alpha)
    raise ModelBackendConfigurationError(f"Declared ML backend is unknown: {backend}.")


def _fit_declared_backend(
    *,
    role: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    model_cfg: dict[str, Any],
    fallback_enabled: bool,
    seed: int,
) -> tuple[Any, str]:
    candidates = _backend_candidates(model_cfg, role, fallback_enabled)
    unavailable: ModelBackendUnavailableError | None = None
    for backend in candidates:
        try:
            model = (
                _build_linear_backend(backend, model_cfg, seed)
                if role == "linear"
                else _build_tree_backend(backend, model_cfg, seed)
            )
        except ModelBackendUnavailableError as exc:
            unavailable = exc
            continue
        model.fit(x_train, y_train)
        return model, backend
    if unavailable is not None:
        raise unavailable
    raise ModelBackendConfigurationError(f"No declared {role} backend could be selected.")


def _backend_version(backend: str) -> str:
    package = _backend_package(backend)
    try:
        version = metadata.version(package)
    except metadata.PackageNotFoundError as exc:
        raise ModelBackendUnavailableError(f"Declared {backend} backend version is unavailable.") from exc
    if not version or version.lower() == "unknown":
        raise ModelBackendUnavailableError(f"Declared {backend} backend version is unknown.")
    return version


def _backend_package(backend: str) -> str:
    if backend == "lightgbm":
        return "lightgbm"
    if backend.startswith(("sklearn_", "hist_", "random_")):
        return "scikit-learn"
    if backend == "numpy_ridge":
        return "numpy"
    raise ModelBackendConfigurationError(f"Declared backend is unknown: {backend}.")


def _locked_backend_version(backend: str, dependency_lock: DependencyLock) -> str:
    package = _backend_package(backend)
    locked_version = dependency_lock.pins.get(package)
    if locked_version:
        return locked_version
    raise ModelBackendUnavailableError(f"Declared {backend} backend is not pinned in the dependency lock.")


def _validated_backend_version(backend: str, execution_mode: str, dependency_lock: DependencyLock) -> str:
    actual_version = _backend_version(backend)
    if execution_mode in {"production", "candidate"} and actual_version != _locked_backend_version(backend, dependency_lock):
        raise ModelBackendUnavailableError(
            f"Declared {backend} backend version does not match the dependency lock."
        )
    return actual_version


def _prepare_matrices(
    train_df: pd.DataFrame,
    score_df: pd.DataFrame,
    feature_columns: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    missing = [col for col in feature_columns if col not in train_df.columns or col not in score_df.columns]
    if missing:
        raise KeyError(f"feature columns not found in DataFrame: {missing}")
    features = train_df[feature_columns].replace([np.inf, -np.inf], np.nan)
    medians = features.median().fillna(0.0)
    x_train = features.fillna(medians).to_numpy(dtype=float)
    x_score = (
        score_df[feature_columns]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(medians)
        .to_numpy(dtype=float)
    )
    y_train = train_df["blended_target"].to_numpy(dtype=float)
    return x_train, y_train, x_score


def fit_predict_models(
    train_df: pd.DataFrame,
    score_df: pd.DataFrame,
    feature_columns: list[str],
    config: dict[str, Any],
    source_revision: str | None = None,
) -> ModelPredictionResult:
    """Fit the linear baseline and ML model, then score a new panel slice."""
    model_cfg, execution_mode, fallback_enabled = _model_config(config)
    seed = _seed(model_cfg)
    model_family = _model_family(model_cfg)
    dependency_lock = read_dependency_lock(DEPENDENCY_LOCK_PATH)
    _backend_candidates(model_cfg, "linear", fallback_enabled)
    _backend_candidates(model_cfg, "ml", fallback_enabled)
    train_df = train_df.loc[train_df["blended_target"].notna()].copy()
    score_df = score_df.copy()
    min_train_rows = int(model_cfg.get("min_train_rows", 100))

    if train_df.empty or score_df.empty or len(train_df) < min_train_rows:
        if execution_mode in {"production", "candidate"}:
            raise ModelBackendConfigurationError(
                "production and candidate model runs require sufficient training and scoring rows."
            )
        empty = pd.DataFrame(index=score_df.index, columns=["linear_score_raw", "ml_score_raw"], dtype=float)
        return ModelPredictionResult(
            predictions=empty,
            linear_backend="insufficient_data",
            ml_backend="insufficient_data",
            train_rows=len(train_df),
            test_rows=len(score_df),
        )

    x_train, y_train, x_score = _prepare_matrices(train_df, score_df, feature_columns)

    linear_model, linear_backend = _fit_declared_backend(
        role="linear",
        x_train=x_train,
        y_train=y_train,
        model_cfg=model_cfg,
        fallback_enabled=fallback_enabled,
        seed=seed,
    )
    tree_model, ml_backend = _fit_declared_backend(
        role="ml",
        x_train=x_train,
        y_train=y_train,
        model_cfg=model_cfg,
        fallback_enabled=fallback_enabled,
        seed=seed,
    )
    linear_backend_version = _validated_backend_version(linear_backend, execution_mode, dependency_lock)
    ml_backend_version = _validated_backend_version(ml_backend, execution_mode, dependency_lock)

    predictions = pd.DataFrame(index=score_df.index)
    predictions["linear_score_raw"] = linear_model.predict(x_score)
    predictions["ml_score_raw"] = tree_model.predict(x_score)
    model_run_manifest = build_model_run_manifest(
        model_family=model_family,
        backends={
            "linear": {"name": linear_backend, "version": linear_backend_version},
            "ml": {"name": ml_backend, "version": ml_backend_version},
        },
        feature_columns=feature_columns,
        label_column="blended_target",
        train_df=train_df,
        predictions=predictions,
        config={
            "data": config.get("data"),
            "universe": config.get("universe"),
            "feature_engineering": config.get("feature_engineering"),
            "labels": config.get("labels"),
            "walkforward": config.get("walkforward"),
            "model": model_cfg,
        },
        source_revision=source_revision or resolve_clean_source_revision(),
        seed=seed,
        dependency_lock=dependency_lock,
    )
    return ModelPredictionResult(
        predictions=predictions,
        linear_backend=linear_backend,
        ml_backend=ml_backend,
        train_rows=len(train_df),
        test_rows=len(score_df),
        model_run_manifest=model_run_manifest,
    )
