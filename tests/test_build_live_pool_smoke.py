from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.release_contract import validate_release_outputs
from src.export import export_strategy_artifact_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "build_live_pool_smoke"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_live_pool.py"
SPEC = importlib.util.spec_from_file_location("build_live_pool_script", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_config(path: Path, output_dir: Path) -> None:
    fixture_root = output_dir.parent
    path.write_text(
        "\n".join(
            [
                "project:",
                '  name: "crypto-live-pool-pipelines"',
                "data:",
                f'  raw_dir: "{fixture_root / "raw"}"',
                f'  cache_dir: "{fixture_root / "cache"}"',
                f'  processed_dir: "{fixture_root / "processed"}"',
                f'  models_dir: "{fixture_root / "models"}"',
                f'  reports_dir: "{fixture_root / "reports"}"',
                f'  output_dir: "{output_dir}"',
                "export:",
                "  live_pool_size: 5",
                "  save_legacy_live_pool: true",
                "publish:",
                '  source_project: "crypto-live-pool-pipelines"',
                "",
            ]
        ),
        encoding="utf-8",
    )


class BuildLivePoolSmokeTests(unittest.TestCase):
    def test_build_live_pool_cli_writes_fixture_outputs_and_validates_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_root = Path(tmp_dir)
            output_dir = temp_root / "output"
            config_path = temp_root / "smoke_config.yaml"
            write_config(config_path, output_dir)

            def fake_build_live_pool_outputs(config, as_of_date=None, universe_mode=None):
                output_path = config["paths"].output_dir
                output_path.mkdir(parents=True, exist_ok=True)
                for fixture_file in FIXTURE_ROOT.iterdir():
                    shutil.copy2(fixture_file, output_path / fixture_file.name)
                live_payload = json.loads((output_path / "live_pool.json").read_text(encoding="utf-8"))
                export_strategy_artifact_manifest(output_dir=output_path, live_pool=live_payload)
                return {
                    "as_of_date": MODULE.pd.Timestamp("2026-03-13"),
                    "train_start_date": MODULE.pd.Timestamp("2024-01-01"),
                    "train_end_date": MODULE.pd.Timestamp("2026-02-12"),
                    "linear_backend": "fixture_linear",
                    "ml_backend": "fixture_ml",
                    "universe_mode": universe_mode or "core_major",
                    "live_payload": live_payload,
                }

            with patch.object(MODULE, "build_live_pool_outputs", side_effect=fake_build_live_pool_outputs), patch.object(
                sys,
                "argv",
                [
                    "build_live_pool.py",
                    "--config",
                    str(config_path),
                    "--universe-mode",
                    "core_major",
                    "--allow-stale",
                ],
            ):
                MODULE.main()

            validation = validate_release_outputs(
                output_dir,
                expected_mode="core_major",
                expected_source_project="crypto-live-pool-pipelines",
                expected_pool_size=5,
                require_artifact_manifest=True,
                require_freshness=False,
            )

        self.assertTrue(validation["ok"])
        self.assertTrue(validation["artifact_manifest_present"])
        self.assertEqual(validation["version"], "2026-03-13-core_major")
        self.assertEqual(validation["pool_size"], 5)


if __name__ == "__main__":
    unittest.main()
