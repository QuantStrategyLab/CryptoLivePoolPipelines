from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

_logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import lightgbm as lgb
except (ModuleNotFoundError, OSError):  # pragma: no cover - optional dependency
    lgb = None
    _logger.info("lightgbm not available; tree model will fall back")

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
    _logger.info("sklearn not available; falling back to numpy ridge")


@dataclass
class ModelPredictionResult:
    predictions: pd.DataFrame
    linear_backend: str
    ml_backend: str
    train_rows: int
    test_rows: int


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


def _fit_linear_model(x_train: np.ndarray, y_train: np.ndarray, config: dict[str, Any]) -> tuple[Any, str]:
    model_cfg = config.get("model", {})
    linear_model_name = str(model_cfg.get("linear_model", "ridge")).lower()
    ridge_alpha = float(model_cfg.get("ridge_alpha", 1.0))
    if Pipeline is not None and StandardScaler is not None and Ridge is not None:
        if linear_model_name == "elasticnet" and ElasticNet is not None:
            model = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    (
                        "model",
                        ElasticNet(
                            alpha=float(model_cfg.get("elasticnet_alpha", 0.01)),
                            l1_ratio=float(model_cfg.get("elasticnet_l1_ratio", 0.5)),
                            random_state=int(model_cfg.get("random_state", 42)),
                            max_iter=5000,
                        ),
                    ),
                ]
            )
            backend = "sklearn_elasticnet"
        else:
            model = Pipeline(
                [
                    ("scaler", StandardScaler()),
                    ("model", Ridge(alpha=ridge_alpha)),
                ]
            )
            backend = "sklearn_ridge"
    else:
        _logger.info("sklearn not available; using numpy ridge for linear model")
        model = NumpyRidgeRegressor(alpha=ridge_alpha)
        backend = "numpy_ridge"

    model.fit(x_train, y_train)
    return model, backend


def _fit_tree_model(x_train: np.ndarray, y_train: np.ndarray, config: dict[str, Any]) -> tuple[Any, str]:
    model_cfg = config.get("model", {})
    ridge_alpha = float(model_cfg.get("ridge_alpha", 1.0))
    if bool(model_cfg.get("use_lightgbm", True)) and lgb is not None:
        params = dict(model_cfg.get("lightgbm_params", {}))
        model = lgb.LGBMRegressor(**params)
        backend = "lightgbm"
    elif HistGradientBoostingRegressor is not None:
        params = dict(model_cfg.get("hist_gbm_params", {}))
        model = HistGradientBoostingRegressor(**params)
        backend = "hist_gradient_boosting"
        _logger.info("lightgbm not available; using sklearn HistGradientBoostingRegressor")
    elif RandomForestRegressor is not None:
        params = dict(model_cfg.get("random_forest_params", {}))
        model = RandomForestRegressor(**params)
        backend = "random_forest"
        _logger.info("tree boosting not available; using sklearn RandomForestRegressor")
    else:
        _logger.warning("no tree model backend available; falling back to numpy ridge for ML model")
        model = NumpyRidgeRegressor(alpha=ridge_alpha)
        backend = "numpy_ridge_fallback"

    model.fit(x_train, y_train)
    return model, backend


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
) -> ModelPredictionResult:
    """Fit the linear baseline and ML model, then score a new panel slice."""
    train_df = train_df.loc[train_df["blended_target"].notna()].copy()
    score_df = score_df.copy()
    min_train_rows = int(config.get("model", {}).get("min_train_rows", 100))

    if train_df.empty or score_df.empty or len(train_df) < min_train_rows:
        empty = pd.DataFrame(index=score_df.index, columns=["linear_score_raw", "ml_score_raw"], dtype=float)
        return ModelPredictionResult(
            predictions=empty,
            linear_backend="insufficient_data",
            ml_backend="insufficient_data",
            train_rows=len(train_df),
            test_rows=len(score_df),
        )

    x_train, y_train, x_score = _prepare_matrices(train_df, score_df, feature_columns)

    linear_model, linear_backend = _fit_linear_model(x_train, y_train, config)
    tree_model, ml_backend = _fit_tree_model(x_train, y_train, config)

    predictions = pd.DataFrame(index=score_df.index)
    predictions["linear_score_raw"] = linear_model.predict(x_score)
    predictions["ml_score_raw"] = tree_model.predict(x_score)
    return ModelPredictionResult(
        predictions=predictions,
        linear_backend=linear_backend,
        ml_backend=ml_backend,
        train_rows=len(train_df),
        test_rows=len(score_df),
    )
