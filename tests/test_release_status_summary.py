from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_release_status_summary.py"
SPEC = importlib.util.spec_from_file_location("release_status_summary", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReleaseStatusSummaryTests(unittest.TestCase):
    def write_outputs(
        self,
        root: Path,
        *,
        include_manifest: bool = True,
        include_artifact_manifest: bool = True,
    ) -> Path:
        output_dir = root / "data" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        symbols = ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "SOLUSDT"]
        symbol_map = {symbol: {"base_asset": symbol[:-4]} for symbol in symbols}
        version = "2026-03-13-core_major"

        write_json(output_dir / "latest_universe.json", {"as_of_date": "2026-03-13", "symbols": symbols + ["XRPUSDT"]})
        write_json(
            output_dir / "live_pool.json",
            {
                "as_of_date": "2026-03-13",
                "version": version,
                "mode": "core_major",
                "pool_size": 5,
                "symbols": symbols,
                "symbol_map": symbol_map,
                "source_project": "crypto-live-pool-pipelines",
            },
        )
        write_json(
            output_dir / "live_pool_legacy.json",
            {
                "as_of_date": "2026-03-13",
                "version": version,
                "mode": "core_major",
                "pool_size": 5,
                "symbols": symbol_map,
                "symbol_map": symbol_map,
                "source_project": "crypto-live-pool-pipelines",
            },
        )
        pd.DataFrame(
            [
                {
                    "as_of_date": "2026-03-13",
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

        if include_artifact_manifest:
            write_json(
                output_dir / "artifact_manifest.json",
                {
                    "manifest_type": "strategy_artifact",
                    "contract_version": "crypto_live_pool_rotation.live_pool.v1",
                    "strategy_profile": "crypto_live_pool_rotation",
                    "artifact_type": "live_pool",
                    "artifact_name": "crypto_live_pool_rotation_live_pool",
                    "as_of_date": "2026-03-13",
                    "snapshot_as_of": "2026-03-13",
                    "version": version,
                    "mode": "core_major",
                    "symbol_count": len(symbols),
                    "symbols": symbols,
                    "source_project": "crypto-live-pool-pipelines",
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
            write_json(
                output_dir / "release_manifest.json",
                {
                    "version": version,
                    "mode": "core_major",
                    "dry_run": True,
                    "publish_enabled": False,
                    "as_of_date": "2026-03-13",
                    "release_prefix": f"crypto-live-pool-pipelines/releases/{version}",
                    "current_prefix": "crypto-live-pool-pipelines/current",
                    "firestore": {
                        "collection": "strategy",
                        "document": "CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL",
                        "payload": {
                            "as_of_date": "2026-03-13",
                            "version": version,
                            "mode": "core_major",
                            "pool_size": 5,
                            "symbols": symbols,
                            "symbol_map": symbol_map,
                            "source_project": "crypto-live-pool-pipelines",
                        },
                    },
                },
            )
        return output_dir

    def test_build_release_status_payload_reports_ok_for_consistent_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_outputs(Path(tmp_dir))
            payload = MODULE.build_release_status_payload(
                output_dir,
                max_age_days=45,
                require_freshness=False,
                ranking_preview_size=3,
                reference_date="2026-04-01",
            )
            outputs = MODULE.write_outputs(payload, output_dir)

            self.assertTrue(outputs["json"].exists())
            self.assertTrue(outputs["markdown"].exists())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["official_release"]["version"], "2026-03-13-core_major")
        self.assertEqual(payload["artifact_summary"]["latest_universe_symbol_count"], 6)
        self.assertEqual(payload["artifact_summary"]["artifact_contract_version"], "crypto_live_pool_rotation.live_pool.v1")
        self.assertEqual(len(payload["artifact_summary"]["ranking_preview"]), 3)
        self.assertTrue(payload["validation"]["ok"])

    def test_build_release_status_payload_orders_preview_by_current_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_outputs(Path(tmp_dir))
            ranking_path = output_dir / "latest_ranking.csv"
            ranking = pd.read_csv(ranking_path)
            ranking = ranking.iloc[[2, 0, 1, 3, 4]]
            ranking.to_csv(ranking_path, index=False)
            artifact_manifest_path = output_dir / "artifact_manifest.json"
            artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
            artifact_manifest["artifacts"]["latest_ranking"]["sha256"] = sha256_file(ranking_path)
            write_json(artifact_manifest_path, artifact_manifest)

            payload = MODULE.build_release_status_payload(
                output_dir,
                max_age_days=45,
                require_freshness=False,
                ranking_preview_size=3,
                reference_date="2026-04-01",
            )

        self.assertEqual(
            [row["symbol"] for row in payload["artifact_summary"]["ranking_preview"]],
            ["TRXUSDT", "ETHUSDT", "BCHUSDT"],
        )

    def test_build_release_status_payload_reports_error_when_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_outputs(Path(tmp_dir), include_manifest=False)
            payload = MODULE.build_release_status_payload(
                output_dir,
                max_age_days=45,
                require_freshness=False,
            )
            markdown = MODULE.render_markdown(payload)

        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["validation"]["ok"])
        self.assertTrue(any("release_manifest.json" in item for item in payload["validation"]["errors"]))
        self.assertIn("## Validation", markdown)

    def test_build_release_status_payload_reports_error_when_artifact_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_outputs(Path(tmp_dir), include_artifact_manifest=False)
            payload = MODULE.build_release_status_payload(
                output_dir,
                max_age_days=45,
                require_freshness=False,
            )

        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["validation"]["artifact_manifest_present"])
        self.assertTrue(any("artifact_manifest.json" in item for item in payload["validation"]["errors"]))


if __name__ == "__main__":
    unittest.main()
