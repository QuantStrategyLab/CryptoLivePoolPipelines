from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.export import build_strategy_artifact_manifest
from src.model_run_manifest import (
    ModelRunManifestError,
    build_model_run_manifest,
    canonical_model_run_manifest_digest,
    read_dependency_lock,
    validate_model_run_manifest,
)
from src.models import (
    ModelBackendConfigurationError,
    ModelBackendUnavailableError,
    fit_predict_models,
)
from src.publish import load_release_artifacts
from src.release_contract import _validate_runtime_evidence_identity as validate_release_identity


class ModelRunManifestTests(unittest.TestCase):
    @staticmethod
    def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
        train_index = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2026-01-01"), "BTCUSDT"),
                (pd.Timestamp("2026-01-01"), "ETHUSDT"),
                (pd.Timestamp("2026-01-02"), "BTCUSDT"),
                (pd.Timestamp("2026-01-02"), "ETHUSDT"),
            ],
            names=["date", "symbol"],
        )
        score_index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2026-01-03"), "BTCUSDT")], names=["date", "symbol"]
        )
        train = pd.DataFrame(
            {"feature_a": [1.0, 2.0, 3.0, 4.0], "blended_target": [0.1, 0.2, 0.3, 0.4]},
            index=train_index,
        )
        score = pd.DataFrame({"feature_a": [5.0]}, index=score_index)
        return train, score

    @staticmethod
    def _config(**model_updates: object) -> dict[str, object]:
        model = {
            "execution_mode": "production",
            "model_family": "crypto_live_pool_dual_regressor",
            "linear_backend": "numpy_ridge",
            "ml_backend": "numpy_ridge",
            "random_state": 42,
            "min_train_rows": 1,
            "ridge_alpha": 1.0,
        }
        model.update(model_updates)
        return {
            "data": {"start_date": "2020-01-01", "end_date": "2026-01-03"},
            "universe": {"live_mode": "core_major", "minimum_history_days": 365},
            "feature_engineering": {"breadth_min_names": 10},
            "model": model,
            "labels": {"horizons": [1]},
            "walkforward": {"train_window_days": 2, "purge_days": None},
        }

    def _manifest(self, **updates: object) -> dict[str, object]:
        train, score = self._frames()
        params: dict[str, object] = {
            "model_family": "crypto_live_pool_dual_regressor",
            "backends": {
                "linear": {"name": "numpy_ridge", "version": "2.4.6"},
                "ml": {"name": "numpy_ridge", "version": "2.4.6"},
            },
            "feature_columns": ["feature_a"],
            "label_column": "blended_target",
            "train_df": train,
            "predictions": pd.DataFrame(
                {"linear_score_raw": [0.5], "ml_score_raw": [0.5]}, index=score.index
            ),
            "config": self._config(),
            "source_revision": "a" * 40,
            "seed": 42,
            "dependency_lock": read_dependency_lock(
                Path(__file__).resolve().parents[1] / "requirements-lock.txt"
            ),
        }
        params.update(updates)
        return build_model_run_manifest(**params)

    def test_production_requires_explicit_declared_backends(self) -> None:
        train, score = self._frames()
        config = self._config()
        config["model"].pop("ml_backend")  # type: ignore[index]

        with self.assertRaises(ModelBackendConfigurationError):
            fit_predict_models(train, score, ["feature_a"], config)

    def test_production_backend_unavailable_fails_closed_without_sklearn_fallback(self) -> None:
        train, score = self._frames()
        config = self._config(ml_backend="lightgbm")

        with patch("src.models.lgb", None), self.assertRaises(ModelBackendUnavailableError):
            fit_predict_models(train, score, ["feature_a"], config)

    def test_production_backend_version_drift_fails_closed(self) -> None:
        train, score = self._frames()
        with patch("src.models._backend_version", return_value="0.0.0"), self.assertRaises(
            ModelBackendUnavailableError
        ):
            fit_predict_models(train, score, ["feature_a"], self._config())

    def test_compatibility_fallback_enabled_must_be_boolean(self) -> None:
        train, score = self._frames()
        with self.assertRaisesRegex(ModelBackendConfigurationError, "boolean"):
            fit_predict_models(
                train,
                score,
                ["feature_a"],
                self._config(execution_mode="research", compatibility_fallback_enabled="true"),
            )

    def test_research_fallback_must_be_explicit_and_records_actual_backend(self) -> None:
        train, score = self._frames()
        config = self._config(
            execution_mode="research",
            ml_backend="lightgbm",
            compatibility_fallback_enabled=True,
            compatibility_fallback_backends={"ml": ["numpy_ridge"]},
        )

        with (
            patch("src.models.lgb", None),
            patch("src.models.resolve_clean_source_revision", return_value="a" * 40),
        ):
            result = fit_predict_models(train, score, ["feature_a"], config)

        self.assertEqual(result.ml_backend, "numpy_ridge")
        self.assertEqual(result.model_run_manifest["backends"]["ml"]["name"], "numpy_ridge")

    def test_manifest_rejects_unknown_nonfinite_and_missing_required_fields(self) -> None:
        with self.assertRaisesRegex(ModelRunManifestError, "unknown"):
            self._manifest(
                backends={
                    "linear": {"name": "unknown", "version": "2.4.6"},
                    "ml": {"name": "numpy_ridge", "version": "2.4.6"},
                }
            )
        with self.assertRaisesRegex(ModelRunManifestError, "finite"):
            self._manifest(seed=float("nan"))
        with self.assertRaisesRegex(ModelRunManifestError, "feature"):
            self._manifest(feature_columns=[])

    def test_manifest_digest_changes_for_model_config_or_prediction_change(self) -> None:
        baseline = self._manifest()
        changed_config = self._manifest(config=self._config(ridge_alpha=2.0))
        train, score = self._frames()
        changed_prediction = self._manifest(
            predictions=pd.DataFrame(
                {"linear_score_raw": [0.6], "ml_score_raw": [0.5]}, index=score.index
            )
        )

        self.assertNotEqual(
            canonical_model_run_manifest_digest(baseline),
            canonical_model_run_manifest_digest(changed_config),
        )
        self.assertNotEqual(
            canonical_model_run_manifest_digest(baseline),
            canonical_model_run_manifest_digest(changed_prediction),
        )
        self.assertEqual(train.index.names, ["date", "symbol"])

    def test_config_projection_binds_every_model_relevant_section(self) -> None:
        baseline = self._manifest()
        self.assertEqual(
            set(baseline["config_projection"]),
            {"data", "universe", "features", "labels", "walkforward", "model", "seed"},
        )
        for section, key, value in (
            ("data", "end_date", "2026-01-04"),
            ("universe", "live_mode", "broad_liquid"),
            ("feature_engineering", "breadth_min_names", 11),
            ("labels", "horizons", [2]),
            ("walkforward", "train_window_days", 3),
            ("model", "ridge_alpha", 2.0),
        ):
            changed_config = deepcopy(self._config())
            changed_config[section][key] = value
            self.assertNotEqual(
                baseline["config"]["sha256"],
                self._manifest(config=changed_config)["config"]["sha256"],
                section,
            )
        self.assertNotEqual(
            baseline["config"]["sha256"],
            self._manifest(seed=43)["config"]["sha256"],
        )

    def test_lock_snapshot_is_single_read_and_rejects_ambiguous_pins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "requirements-lock.txt"
            lock_bytes = b"numpy==2.4.6\nscikit-learn==1.9.0\nlightgbm==4.7.0\n"
            lock_path.write_bytes(lock_bytes)
            lock = read_dependency_lock(lock_path)
            lock_path.write_bytes(b"numpy==9.9.9\n")
            manifest = self._manifest(dependency_lock=lock)
            self.assertEqual(manifest["dependency_lock"]["sha256"], hashlib.sha256(lock_bytes).hexdigest())

            for invalid_contents in (
                b"numpy==2.4.6\nnumpy==2.4.7\n",
                b"numpy==2.4.6 # comment\n",
                b"numpy==2.4.6; python_version >= '3.11'\n",
            ):
                lock_path.write_bytes(invalid_contents)
                with self.assertRaisesRegex(ModelRunManifestError, "lock"):
                    read_dependency_lock(lock_path)

    def test_validator_rejects_unknown_or_internally_inconsistent_fields(self) -> None:
        manifest = self._manifest()
        forged_source = deepcopy(manifest)
        forged_source["source"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ModelRunManifestError, "source.sha256"):
            validate_model_run_manifest(forged_source)

        unknown_top_level = deepcopy(manifest)
        unknown_top_level["unexpected"] = True
        with self.assertRaisesRegex(ModelRunManifestError, "unknown fields"):
            validate_model_run_manifest(unknown_top_level)

        invalid_window = deepcopy(manifest)
        invalid_window["training_window"]["rows"] = 0
        with self.assertRaisesRegex(ModelRunManifestError, "training_window.rows"):
            validate_model_run_manifest(invalid_window)

        invalid_config_digest = deepcopy(manifest)
        invalid_config_digest["config"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ModelRunManifestError, "config.sha256"):
            validate_model_run_manifest(invalid_config_digest)

        invalid_linear_backend = deepcopy(manifest)
        invalid_linear_backend["backends"]["linear"]["name"] = "lightgbm"
        with self.assertRaisesRegex(ModelRunManifestError, "linear backend"):
            validate_model_run_manifest(invalid_linear_backend)

        invalid_ml_backend = deepcopy(manifest)
        invalid_ml_backend["backends"]["ml"]["name"] = "sklearn_ridge"
        with self.assertRaisesRegex(ModelRunManifestError, "ml backend"):
            validate_model_run_manifest(invalid_ml_backend)

        invalid_backend_version = deepcopy(manifest)
        invalid_backend_version["backends"]["ml"]["version"] = "fake"
        with self.assertRaisesRegex(ModelRunManifestError, "version"):
            validate_model_run_manifest(invalid_backend_version)

    def test_same_inputs_produce_same_predictions_and_manifest(self) -> None:
        train, score = self._frames()
        config = self._config()

        with patch("src.models.resolve_clean_source_revision", return_value="a" * 40):
            first = fit_predict_models(train, score, ["feature_a"], config)
            second = fit_predict_models(train, score, ["feature_a"], config)

        pd.testing.assert_frame_equal(first.predictions, second.predictions)
        self.assertEqual(first.model_run_manifest, second.model_run_manifest)

    def test_runtime_identity_binds_canonical_model_run_manifest_digest(self) -> None:
        model_run_manifest = self._manifest()
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            live_pool = {
                "as_of_date": "2026-01-03",
                "version": "2026-01-03-core_major",
                "mode": "core_major",
                "symbols": ["BTCUSDT"],
                "source_project": "crypto-live-pool-pipelines",
            }
            (output_dir / "live_pool.json").write_text(json.dumps(live_pool), encoding="utf-8")
            legacy_live_pool = dict(live_pool, symbols={"BTCUSDT": {"base_asset": "BTC"}})
            (output_dir / "live_pool_legacy.json").write_text(
                json.dumps(legacy_live_pool), encoding="utf-8"
            )
            (output_dir / "latest_universe.json").write_text(json.dumps({"symbols": ["BTCUSDT"]}), encoding="utf-8")
            (output_dir / "latest_ranking.csv").write_text("symbol\nBTCUSDT\n", encoding="utf-8")

            with patch("src.export.resolve_clean_source_revision", return_value="a" * 40):
                artifact_manifest = build_strategy_artifact_manifest(
                    output_dir=output_dir,
                    live_pool=live_pool,
                    input_timestamp="2026-01-03",
                    model_run_manifest=model_run_manifest,
                )
            (output_dir / "artifact_manifest.json").write_text(
                json.dumps(artifact_manifest), encoding="utf-8"
            )

            expected_digest = canonical_model_run_manifest_digest(model_run_manifest)
            binding = artifact_manifest["runtime_evidence_identity"]["model_run_manifest"]
            self.assertEqual(binding["sha256"], expected_digest)
            self.assertEqual(
                hashlib.sha256((output_dir / "model_run_manifest.json").read_bytes()).hexdigest(),
                expected_digest,
            )
            self.assertEqual(
                load_release_artifacts(output_dir, "core_major").runtime_evidence_identity[
                    "model_run_manifest"
                ]["sha256"],
                expected_digest,
            )
            forged_manifest = deepcopy(model_run_manifest)
            forged_manifest["source"] = {
                "revision": "b" * 40,
                "sha256": hashlib.sha256(("b" * 40).encode("utf-8")).hexdigest(),
            }
            forged_digest = canonical_model_run_manifest_digest(forged_manifest)
            artifact_manifest["model_run_manifest"] = forged_manifest
            artifact_manifest["runtime_evidence_identity"]["model_run_manifest"]["sha256"] = forged_digest
            (output_dir / "model_run_manifest.json").write_bytes(
                json.dumps(forged_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            (output_dir / "artifact_manifest.json").write_text(
                json.dumps(artifact_manifest), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "source revision mismatch"):
                load_release_artifacts(output_dir, "core_major")
            artifact_manifest["model_run_manifest"] = model_run_manifest
            artifact_manifest["runtime_evidence_identity"]["model_run_manifest"]["sha256"] = expected_digest
            (output_dir / "model_run_manifest.json").write_bytes(
                json.dumps(model_run_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            (output_dir / "artifact_manifest.json").write_text(
                json.dumps(artifact_manifest), encoding="utf-8"
            )
            (output_dir / "model_run_manifest.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "model_run_manifest digest mismatch"):
                load_release_artifacts(output_dir, "core_major")

    def test_source_revision_must_match_runtime_identity_in_export_publish_and_release(self) -> None:
        model_run_manifest = self._manifest(source_revision="b" * 40)
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            live_pool = {
                "as_of_date": "2026-01-03",
                "version": "2026-01-03-core_major",
                "mode": "core_major",
                "symbols": ["BTCUSDT"],
                "source_project": "crypto-live-pool-pipelines",
            }
            for name, payload in (
                ("live_pool.json", live_pool),
                ("live_pool_legacy.json", dict(live_pool, symbols={"BTCUSDT": {"base_asset": "BTC"}})),
                ("latest_universe.json", {"as_of_date": "2026-01-03", "symbols": ["BTCUSDT"]}),
            ):
                (output_dir / name).write_text(json.dumps(payload), encoding="utf-8")
            (output_dir / "latest_ranking.csv").write_text("as_of_date,symbol\n2026-01-03,BTCUSDT\n", encoding="utf-8")

            with patch("src.export.resolve_clean_source_revision", return_value="a" * 40), self.assertRaisesRegex(
                ValueError, "source revision"
            ):
                build_strategy_artifact_manifest(
                    output_dir=output_dir,
                    live_pool=live_pool,
                    input_timestamp="2026-01-03",
                    model_run_manifest=model_run_manifest,
                )

            with self.assertRaises(TypeError):
                build_strategy_artifact_manifest(
                    output_dir=output_dir,
                    live_pool=live_pool,
                    input_timestamp="2026-01-03",
                    model_run_manifest=model_run_manifest,
                    source_revision="b" * 40,
                )

            artifact_manifest = {
                "strategy_profile": "crypto_live_pool_rotation",
                "contract_version": "crypto_live_pool_rotation.live_pool.v1",
                "model_run_manifest": model_run_manifest,
                "artifacts": {},
            }
            identity = {
                "strategy_profile": "crypto_live_pool_rotation",
                "mode": "core_major",
                "source_revision": "a" * 40,
                "input_timestamp": "2026-01-03T00:00:00Z",
                "artifact_contract": "crypto_live_pool_rotation.live_pool.v1",
                "artifact_version": "2026-01-03-core_major",
                "artifacts": {},
                "model_run_manifest": {
                    "contract_version": "model_run_manifest.v1",
                    "path": "model_run_manifest.json",
                    "sha256": canonical_model_run_manifest_digest(model_run_manifest),
                },
            }
            errors: list[str] = []
            validate_release_identity(
                {"runtime_evidence_identity": identity},
                artifact_manifest,
                live_pool_mode="core_major",
                live_pool_version="2026-01-03-core_major",
                output_path=output_dir,
                errors=errors,
            )
        self.assertTrue(any("source_revision" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
