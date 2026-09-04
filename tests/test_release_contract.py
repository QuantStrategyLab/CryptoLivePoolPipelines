from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.publish import (
    PublishSettings,
    ReleaseArtifacts,
    build_firestore_payload,
    build_release_manifest,
    build_storage_layout,
    ensure_publish_preflight,
    load_release_artifacts,
    upload_release_artifacts,
)
from src.model_run_manifest import (
    build_model_run_manifest,
    canonical_model_run_manifest_digest,
    read_dependency_lock,
    write_model_run_manifest,
)
from src.release_contract import validate_release_outputs


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_publish_settings() -> PublishSettings:
    return PublishSettings(
        enabled=False,
        dry_run=True,
        mode="core_major",
        project_id=None,
        cloud_bucket=None,
        cloud_root_prefix="crypto-live-pool-pipelines",
        firestore_collection="strategy",
        firestore_document="CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
        source_project="crypto-live-pool-pipelines",
        upload_current_pointer=False,
    )


class ReleaseContractValidationTests(unittest.TestCase):
    @staticmethod
    def _model_run_manifest() -> dict[str, object]:
        train_index = pd.MultiIndex.from_tuples(
            [
                (pd.Timestamp("2026-03-11"), "BTCUSDT"),
                (pd.Timestamp("2026-03-11"), "ETHUSDT"),
            ],
            names=["date", "symbol"],
        )
        score_index = pd.MultiIndex.from_tuples(
            [(pd.Timestamp("2026-03-13"), "BTCUSDT")],
            names=["date", "symbol"],
        )
        return build_model_run_manifest(
            model_family="crypto_live_pool_dual_regressor",
            backends={
                "linear": {"name": "numpy_ridge", "version": "2.4.6"},
                "ml": {"name": "numpy_ridge", "version": "2.4.6"},
            },
            feature_columns=["feature_a"],
            label_column="blended_target",
            train_df=pd.DataFrame(
                {"feature_a": [1.0, 2.0], "blended_target": [0.1, 0.2]},
                index=train_index,
            ),
            predictions=pd.DataFrame(
                {"linear_score_raw": [0.3], "ml_score_raw": [0.4]},
                index=score_index,
            ),
            config={
                "data": {"start_date": "2020-01-01", "end_date": "2026-03-13"},
                "universe": {"live_mode": "core_major"},
                "feature_engineering": {"breadth_min_names": 10},
                "labels": {"horizons": [1]},
                "walkforward": {"train_window_days": 2},
                "model": {"execution_mode": "production", "random_state": 42},
            },
            source_revision="a" * 40,
            seed=42,
            dependency_lock=read_dependency_lock(
                Path(__file__).resolve().parents[1] / "requirements-lock.txt"
            ),
        )

    def build_outputs(
        self,
        root: Path,
        *,
        as_of_date: str = "2026-03-13",
        mode: str = "core_major",
        source_project: str = "crypto-live-pool-pipelines",
        include_manifest: bool = False,
        include_runtime_evidence_identity: bool = False,
    ) -> None:
        output_dir = root / "data" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        symbols = ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "SOLUSDT"]
        symbol_map = {symbol: {"base_asset": symbol[:-4]} for symbol in symbols}
        version = f"{as_of_date}-{mode}"

        write_json(output_dir / "latest_universe.json", {"as_of_date": as_of_date, "symbols": symbols + ["XRPUSDT"]})
        write_json(
            output_dir / "live_pool.json",
            {
                "as_of_date": as_of_date,
                "version": version,
                "mode": mode,
                "pool_size": len(symbols),
                "symbols": symbols,
                "symbol_map": symbol_map,
                "source_project": source_project,
            },
        )
        write_json(
            output_dir / "live_pool_legacy.json",
            {
                "as_of_date": as_of_date,
                "version": version,
                "mode": mode,
                "pool_size": len(symbols),
                "symbols": symbol_map,
                "symbol_map": symbol_map,
                "source_project": source_project,
            },
        )

        pd.DataFrame(
            [
                {
                    "as_of_date": as_of_date,
                    "symbol": symbol,
                    "rule_score": 1.0 - index * 0.1,
                    "linear_score": 0.9 - index * 0.1,
                    "ml_score": 0.8 - index * 0.1,
                    "final_score": 1.0 - index * 0.1,
                    "regime": "risk_off",
                    "confidence": 0.7,
                    "selected_flag": True,
                    "current_rank": index + 1,
                }
                for index, symbol in enumerate(symbols)
            ]
        ).to_csv(output_dir / "latest_ranking.csv", index=False)

        write_json(
            output_dir / "artifact_manifest.json",
            {
                "manifest_type": "strategy_artifact",
                "contract_version": "crypto_live_pool_rotation.live_pool.v1",
                "strategy_profile": "crypto_live_pool_rotation",
                "artifact_type": "live_pool",
                "artifact_name": "crypto_live_pool_rotation_live_pool",
                "as_of_date": as_of_date,
                "snapshot_as_of": as_of_date,
                "version": version,
                "mode": mode,
                "symbol_count": len(symbols),
                "symbols": symbols,
                "source_project": source_project,
                "generated_at": "2026-03-13T00:00:00+00:00",
                "primary_artifact": "live_pool",
                "artifacts": {
                    "latest_universe": {
                        "path": "latest_universe.json",
                        "sha256": sha256_file(output_dir / "latest_universe.json"),
                    },
                    "latest_ranking": {
                        "path": "latest_ranking.csv",
                        "sha256": sha256_file(output_dir / "latest_ranking.csv"),
                    },
                    "live_pool": {
                        "path": "live_pool.json",
                        "sha256": sha256_file(output_dir / "live_pool.json"),
                    },
                    "live_pool_legacy": {
                        "path": "live_pool_legacy.json",
                        "sha256": sha256_file(output_dir / "live_pool_legacy.json"),
                    },
                },
            },
        )

        if include_manifest:
            runtime_evidence_identity = {}
            if include_runtime_evidence_identity:
                artifact_manifest = json.loads(
                    (output_dir / "artifact_manifest.json").read_text(encoding="utf-8")
                )
                model_run_manifest = self._model_run_manifest()
                model_run_digest = write_model_run_manifest(
                    output_dir / "model_run_manifest.json", model_run_manifest
                )
                self.assertEqual(
                    model_run_digest,
                    canonical_model_run_manifest_digest(model_run_manifest),
                )
                runtime_evidence_identity = {
                    "strategy_profile": "crypto_live_pool_rotation",
                    "mode": mode,
                    "source_revision": "a" * 40,
                    "input_timestamp": "2026-03-13T00:00:00Z",
                    "artifact_contract": artifact_manifest["contract_version"],
                    "artifact_version": version,
                    "artifacts": artifact_manifest["artifacts"],
                    "model_run_manifest": {
                        "contract_version": model_run_manifest["contract_version"],
                        "path": "model_run_manifest.json",
                        "sha256": model_run_digest,
                    },
                }
                artifact_manifest["model_run_manifest"] = model_run_manifest
                artifact_manifest["runtime_evidence_identity"] = runtime_evidence_identity
                write_json(output_dir / "artifact_manifest.json", artifact_manifest)
            write_json(
                output_dir / "release_manifest.json",
                {
                    "version": version,
                    "mode": mode,
                    "dry_run": True,
                    "publish_enabled": False,
                    "as_of_date": as_of_date,
                    "release_prefix": f"crypto-live-pool-pipelines/releases/{version}",
                    "current_prefix": "crypto-live-pool-pipelines/current",
                    "artifacts": {},
                    "runtime_evidence_identity": runtime_evidence_identity,
                    "firestore": {
                        "collection": "strategy",
                        "document": "CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
                        "payload": {
                            "as_of_date": as_of_date,
                            "version": version,
                            "mode": mode,
                            "pool_size": len(symbols),
                            "symbols": symbols,
                            "symbol_map": symbol_map,
                            "source_project": source_project,
                            "runtime_evidence_identity": runtime_evidence_identity,
                        },
                    },
                },
            )

    def build_runtime_identity_outputs(self, root: Path) -> Path:
        self.build_outputs(
            root,
            include_manifest=True,
            include_runtime_evidence_identity=True,
        )
        return root / "data" / "output"

    def load_publish_context(
        self, output_dir: Path
    ) -> tuple[ReleaseArtifacts, PublishSettings, dict[str, object]]:
        artifacts = load_release_artifacts(output_dir, "core_major")
        settings = build_publish_settings()
        return artifacts, settings, build_storage_layout(settings, artifacts)

    def test_validate_release_outputs_accepts_consistent_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root, include_manifest=True)

            validation = validate_release_outputs(
                root / "data" / "output",
                expected_mode="core_major",
                expected_source_project="crypto-live-pool-pipelines",
                expected_pool_size=5,
                reference_date="2026-03-14",
                max_age_days=45,
                require_manifest=True,
                require_artifact_manifest=True,
                require_freshness=True,
            )

        self.assertTrue(validation["ok"])
        self.assertTrue(validation["artifact_manifest_present"])
        self.assertEqual(validation["artifact_contract_version"], "crypto_live_pool_rotation.live_pool.v1")
        self.assertEqual(validation["version"], "2026-03-13-core_major")
        self.assertEqual(validation["pool_size"], 5)
        self.assertEqual(validation["age_days"], 1)

    def test_identity_is_canonical_across_artifact_release_and_firestore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(
                root,
                include_manifest=True,
                include_runtime_evidence_identity=True,
            )
            output_dir = root / "data" / "output"
            artifacts = load_release_artifacts(output_dir, "core_major")
            settings = PublishSettings(
                enabled=False,
                dry_run=True,
                mode="core_major",
                project_id=None,
                cloud_bucket=None,
                cloud_root_prefix="crypto-live-pool-pipelines",
                firestore_collection="strategy",
                firestore_document="CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
                source_project="crypto-live-pool-pipelines",
                upload_current_pointer=False,
            )
            storage_layout = build_storage_layout(settings, artifacts)
            firestore_payload = build_firestore_payload(settings, artifacts, storage_layout)
            release_manifest = build_release_manifest(
                settings,
                artifacts,
                storage_layout,
                firestore_payload,
            )

        identity = artifacts.artifact_manifest["runtime_evidence_identity"]
        self.assertEqual(release_manifest["runtime_evidence_identity"], identity)
        self.assertEqual(firestore_payload["runtime_evidence_identity"], identity)

    def test_firestore_payload_preserves_exact_legacy_artifact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = self.build_runtime_identity_outputs(root)
            legacy_path = output_dir / "live_pool_legacy.json"
            legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
            exact_bytes = ("\n" + json.dumps(legacy_payload, separators=(", ", ": ")) + "\n").encode(
                "utf-8"
            )
            legacy_path.write_bytes(exact_bytes)

            manifest_path = output_dir / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            exact_digest = hashlib.sha256(exact_bytes).hexdigest()
            manifest["artifacts"]["live_pool_legacy"]["sha256"] = exact_digest
            manifest["runtime_evidence_identity"]["artifacts"]["live_pool_legacy"][
                "sha256"
            ] = exact_digest
            write_json(manifest_path, manifest)

            artifacts, settings, storage_layout = self.load_publish_context(output_dir)
            firestore_payload = build_firestore_payload(
                settings,
                artifacts,
                storage_layout,
            )

        handoff = firestore_payload["live_pool_legacy_exact_bytes"]
        self.assertEqual(
            handoff["contract_version"],
            "qsl.crypto_live_pool_legacy_exact_bytes.v1",
        )
        self.assertEqual(handoff["encoding"], "utf-8")
        self.assertEqual(handoff["utf8_text"].encode("utf-8"), exact_bytes)

    def test_upload_reuses_validated_legacy_snapshot_after_path_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = self.build_runtime_identity_outputs(root)
            artifacts = load_release_artifacts(output_dir, "core_major")
            settings = PublishSettings(
                enabled=True,
                dry_run=False,
                mode="core_major",
                project_id="test-project",
                cloud_bucket="test-bucket",
                cloud_root_prefix="crypto-live-pool-pipelines",
                firestore_collection="strategy",
                firestore_document="CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
                source_project="crypto-live-pool-pipelines",
                upload_current_pointer=True,
            )
            storage_layout = build_storage_layout(settings, artifacts)
            firestore_payload = build_firestore_payload(
                settings,
                artifacts,
                storage_layout,
            )
            validated_bytes = firestore_payload["live_pool_legacy_exact_bytes"][
                "utf8_text"
            ].encode("utf-8")
            model_bytes = artifacts.model_run_manifest_path.read_bytes()
            artifacts.live_pool_legacy_path.write_bytes(b'{"mutated": true}\n')

            uploaded: dict[str, bytes] = {}

            class FakeStore:
                def write_bytes(self, uri: str, payload: bytes) -> None:
                    uploaded[uri] = payload

            with patch(
                "quant_platform_kit.cloud.get_object_store",
                return_value=FakeStore(),
            ):
                upload_release_artifacts(
                    settings,
                    artifacts,
                    storage_layout,
                    live_pool_legacy_exact_bytes=validated_bytes,
                )

        legacy_objects = storage_layout["objects"]["live_pool_legacy.json"]
        release_bytes = uploaded[legacy_objects["release_uri"]]
        current_bytes = uploaded[legacy_objects["current_uri"]]
        manifest_digest = artifacts.artifact_manifest["artifacts"][
            "live_pool_legacy"
        ]["sha256"]
        identity_digest = artifacts.runtime_evidence_identity["artifacts"][
            "live_pool_legacy"
        ]["sha256"]
        self.assertEqual(release_bytes, validated_bytes)
        self.assertEqual(current_bytes, validated_bytes)
        self.assertEqual(hashlib.sha256(validated_bytes).hexdigest(), manifest_digest)
        self.assertEqual(hashlib.sha256(release_bytes).hexdigest(), manifest_digest)
        self.assertEqual(identity_digest, manifest_digest)

        model_objects = storage_layout["objects"]["model_run_manifest.json"]
        model_digest = artifacts.runtime_evidence_identity["model_run_manifest"]["sha256"]
        self.assertEqual(uploaded[model_objects["release_uri"]], model_bytes)
        self.assertEqual(uploaded[model_objects["current_uri"]], model_bytes)
        self.assertEqual(hashlib.sha256(model_bytes).hexdigest(), model_digest)

    def test_firestore_payload_rejects_mutated_legacy_bytes_with_unchanged_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = self.build_runtime_identity_outputs(root)
            artifacts, settings, storage_layout = self.load_publish_context(output_dir)
            with artifacts.live_pool_legacy_path.open("ab") as handle:
                handle.write(b"\n")

            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                build_firestore_payload(
                    settings,
                    artifacts,
                    storage_layout,
                )

    def test_firestore_payload_rejects_top_level_convenience_field_mismatch(self) -> None:
        for field in ("symbols", "symbol_map"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                output_dir = self.build_runtime_identity_outputs(root)
                artifacts, settings, storage_layout = self.load_publish_context(output_dir)
                if field == "symbols":
                    artifacts.live_pool_legacy[field].pop("TRXUSDT")
                else:
                    artifacts.live_pool_legacy[field]["TRXUSDT"] = {
                        "base_asset": "MISMATCH"
                    }

                with self.assertRaisesRegex(ValueError, "convenience fields"):
                    build_firestore_payload(
                        settings,
                        artifacts,
                        storage_layout,
                    )

    def test_firestore_payload_rejects_pool_size_or_source_project_mismatch(self) -> None:
        cases = {
            "pool_size_mismatch": ("pool_size", 4),
            "pool_size_missing": ("pool_size", None),
            "source_project_mismatch": ("source_project", "wrong-source"),
            "source_project_missing": ("source_project", None),
        }
        for label, (field, value) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                output_dir = self.build_runtime_identity_outputs(root)
                legacy_path = output_dir / "live_pool_legacy.json"
                legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                if value is None:
                    legacy_payload.pop(field)
                else:
                    legacy_payload[field] = value
                write_json(legacy_path, legacy_payload)

                manifest_path = output_dir / "artifact_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                digest = sha256_file(legacy_path)
                manifest["artifacts"]["live_pool_legacy"]["sha256"] = digest
                manifest["runtime_evidence_identity"]["artifacts"][
                    "live_pool_legacy"
                ]["sha256"] = digest
                write_json(manifest_path, manifest)

                artifacts, settings, storage_layout = self.load_publish_context(
                    output_dir
                )
                with self.assertRaisesRegex(ValueError, "convenience fields"):
                    build_firestore_payload(
                        settings,
                        artifacts,
                        storage_layout,
                    )

    def test_firestore_payload_rejects_invalid_legacy_artifact_bytes(self) -> None:
        cases = {
            "invalid_utf8": (b"\xff", "valid UTF-8"),
            "invalid_json": (b"{", "valid JSON"),
            "nan": (b'{"value": NaN}', "valid JSON"),
            "infinity": (b'{"value": Infinity}', "valid JSON"),
            "negative_infinity": (b'{"value": -Infinity}', "valid JSON"),
            "non_object": (b"[]", "JSON object"),
        }
        for label, (invalid_bytes, expected_error) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                output_dir = self.build_runtime_identity_outputs(root)
                artifacts, settings, storage_layout = self.load_publish_context(output_dir)
                artifacts.live_pool_legacy_path.write_bytes(invalid_bytes)

                with self.assertRaisesRegex(ValueError, expected_error):
                    build_firestore_payload(
                        settings,
                        artifacts,
                        storage_layout,
                    )

    def test_firestore_payload_rejects_missing_legacy_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = self.build_runtime_identity_outputs(root)
            artifacts, settings, storage_layout = self.load_publish_context(output_dir)
            artifacts.live_pool_legacy_path.unlink()

            with self.assertRaises(FileNotFoundError):
                build_firestore_payload(
                    settings,
                    artifacts,
                    storage_layout,
                )

    def test_artifact_byte_mutation_breaks_runtime_identity_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(
                root,
                include_manifest=True,
                include_runtime_evidence_identity=True,
            )
            output_dir = root / "data" / "output"
            with (output_dir / "latest_ranking.csv").open("ab") as handle:
                handle.write(b"\n")

            with self.assertRaises(ValueError):
                load_release_artifacts(output_dir, "core_major")

    def test_validate_release_outputs_requires_runtime_evidence_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(
                root,
                include_manifest=True,
                include_runtime_evidence_identity=True,
            )

            validation = validate_release_outputs(
                root / "data" / "output",
                require_manifest=True,
                require_artifact_manifest=True,
                require_runtime_evidence_identity=True,
            )

        self.assertTrue(validation["ok"])

    def test_validate_release_outputs_rejects_incomplete_or_mismatched_runtime_evidence_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(
                root,
                include_manifest=True,
                include_runtime_evidence_identity=True,
            )
            manifest_path = root / "data" / "output" / "release_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            identity = manifest["runtime_evidence_identity"]
            identity.pop("source_revision")
            identity["artifacts"]["live_pool"]["sha256"] = "b" * 64
            write_json(manifest_path, manifest)

            validation = validate_release_outputs(
                root / "data" / "output",
                require_manifest=True,
                require_artifact_manifest=True,
                require_runtime_evidence_identity=True,
            )

        self.assertFalse(validation["ok"])
        self.assertIn(
            "release_manifest.json runtime_evidence_identity missing field: source_revision",
            validation["errors"],
        )
        self.assertIn(
            "release_manifest.json runtime_evidence_identity artifacts.live_pool.sha256 does not match artifact_manifest.json",
            validation["errors"],
        )

    def test_validate_release_outputs_rejects_mismatched_artifact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root)
            manifest_path = root / "data" / "output" / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["contract_version"] = "crypto_live_pool_rotation.live_pool.v0"
            manifest["artifacts"]["live_pool"]["sha256"] = "wrong"
            write_json(manifest_path, manifest)

            validation = validate_release_outputs(
                root / "data" / "output",
                require_artifact_manifest=True,
            )

        self.assertFalse(validation["ok"])
        self.assertIn(
            "artifact_manifest.json contract_version must be crypto_live_pool_rotation.live_pool.v1",
            validation["errors"],
        )
        self.assertIn(
            "artifact_manifest.json artifacts.live_pool.sha256 does not match file content",
            validation["errors"],
        )

    def test_validate_release_outputs_rejects_mismatched_manifest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root, include_manifest=True)
            manifest_path = root / "data" / "output" / "release_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["firestore"]["payload"]["source_project"] = "wrong-source"
            write_json(manifest_path, manifest)

            validation = validate_release_outputs(root / "data" / "output", require_manifest=True)

        self.assertFalse(validation["ok"])
        self.assertIn(
            "release_manifest.json firestore.payload source_project does not match live_pool.json",
            validation["errors"],
        )

    def test_validate_release_outputs_rejects_live_pool_order_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root)
            output_dir = root / "data" / "output"
            live_pool_path = output_dir / "live_pool.json"
            artifact_manifest_path = output_dir / "artifact_manifest.json"

            live_pool = json.loads(live_pool_path.read_text(encoding="utf-8"))
            live_pool["symbols"] = [
                "TRXUSDT",
                "ETHUSDT",
                "NEARUSDT",
                "BCHUSDT",
                "SOLUSDT",
            ]
            write_json(live_pool_path, live_pool)

            artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
            artifact_manifest["symbols"] = live_pool["symbols"]
            artifact_manifest["artifacts"]["live_pool"]["sha256"] = sha256_file(live_pool_path)
            write_json(artifact_manifest_path, artifact_manifest)

            validation = validate_release_outputs(root / "data" / "output", require_artifact_manifest=True)

        self.assertFalse(validation["ok"])
        self.assertIn(
            "live_pool.json symbols must match selected latest_ranking.csv symbols ordered by current_rank",
            validation["errors"],
        )

    def test_validate_release_outputs_rejects_extra_selected_ranking_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root)
            output_dir = root / "data" / "output"
            ranking_path = output_dir / "latest_ranking.csv"
            artifact_manifest_path = output_dir / "artifact_manifest.json"

            ranking = pd.read_csv(ranking_path)
            ranking.loc[len(ranking)] = {
                "as_of_date": "2026-03-13",
                "symbol": "XRPUSDT",
                "rule_score": 0.4,
                "linear_score": 0.3,
                "ml_score": 0.2,
                "final_score": 0.4,
                "regime": "risk_off",
                "confidence": 0.5,
                "selected_flag": True,
                "current_rank": 6,
            }
            ranking.to_csv(ranking_path, index=False)

            artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
            artifact_manifest["artifacts"]["latest_ranking"]["sha256"] = sha256_file(ranking_path)
            write_json(artifact_manifest_path, artifact_manifest)

            validation = validate_release_outputs(root / "data" / "output", require_artifact_manifest=True)

        self.assertFalse(validation["ok"])
        self.assertIn(
            "latest_ranking.csv selected_flag row count must match live_pool.json symbols length",
            validation["errors"],
        )

    def test_validate_release_outputs_rejects_stale_outputs_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root, as_of_date="2026-01-01")

            validation = validate_release_outputs(
                root / "data" / "output",
                reference_date="2026-03-15",
                max_age_days=30,
                require_freshness=True,
            )

        self.assertFalse(validation["ok"])
        self.assertTrue(any("stale" in message for message in validation["errors"]))

    def test_publish_preflight_requires_real_publish_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.build_outputs(root)

            with self.assertRaises(ValueError) as context:
                ensure_publish_preflight(
                    type(
                        "Settings",
                        (),
                        {
                            "mode": "core_major",
                            "source_project": "crypto-live-pool-pipelines",
                            "dry_run": False,
                            "project_id": None,
                            "cloud_bucket": None,
                            "firestore_collection": "strategy",
                            "firestore_document": "CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
                        },
                    )(),
                    root / "data" / "output",
                    expected_pool_size=5,
                    reference_date="2026-03-14",
                )

        self.assertIn("GCP_PROJECT_ID", str(context.exception))


if __name__ == "__main__":
    unittest.main()
