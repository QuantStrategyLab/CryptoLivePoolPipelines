from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.release_contract import validate_release_outputs
import pandas as pd

from src.export import (
    build_strategy_artifact_manifest,
    export_strategy_artifact_manifest,
    resolve_clean_source_revision,
)
from src.pipeline import resolve_scoring_input_timestamp


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
                export_strategy_artifact_manifest(
                    output_dir=output_path,
                    live_pool=live_payload,
                    input_timestamp=MODULE.pd.Timestamp("2026-03-13"),
                )
                return {
                    "as_of_date": MODULE.pd.Timestamp("2026-03-13"),
                    "train_start_date": MODULE.pd.Timestamp("2024-01-01"),
                    "train_end_date": MODULE.pd.Timestamp("2026-02-12"),
                    "linear_backend": "fixture_linear",
                    "ml_backend": "fixture_ml",
                    "universe_mode": universe_mode or "core_major",
                    "live_payload": live_payload,
                }

            with (
                patch.object(MODULE, "build_live_pool_outputs", side_effect=fake_build_live_pool_outputs),
                patch("src.export.resolve_clean_source_revision", return_value="c" * 40),
                patch.object(
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
                ),
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

    def test_manifest_identity_uses_git_head_and_panel_cutoff_not_environment_or_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            for fixture_file in FIXTURE_ROOT.iterdir():
                shutil.copy2(fixture_file, output_dir / fixture_file.name)
            live_payload = json.loads((output_dir / "live_pool.json").read_text(encoding="utf-8"))

            with (
                patch("src.export.resolve_clean_source_revision", return_value="c" * 40),
                patch.dict(os.environ, {"GITHUB_SHA": "d" * 40, "SOURCE_REVISION": "e" * 40}),
            ):
                manifest = build_strategy_artifact_manifest(
                    output_dir=output_dir,
                    live_pool=live_payload,
                    input_timestamp=pd.Timestamp("2026-03-13"),
                    generated_at=pd.Timestamp("2030-01-01T12:34:56Z"),
                )

        identity = manifest["runtime_evidence_identity"]
        self.assertEqual(identity["source_revision"], "c" * 40)
        self.assertEqual(identity["input_timestamp"], "2026-03-13T00:00:00Z")
        self.assertNotEqual(identity["source_revision"], "d" * 40)
        self.assertNotEqual(identity["input_timestamp"], manifest["generated_at"])
        self.assertEqual(identity["artifacts"], manifest["artifacts"])
        self.assertEqual(
            set(identity["artifacts"]),
            {"live_pool", "live_pool_legacy", "latest_ranking", "latest_universe"},
        )

    def test_resolve_clean_source_revision_rejects_dirty_or_unresolvable_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=repo,
                check=True,
            )
            revision = resolve_clean_source_revision(repo)
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

            (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                resolve_clean_source_revision(repo)

            with self.assertRaises(RuntimeError):
                resolve_clean_source_revision(Path(tmp_dir) / "not-a-repo")

    def test_scoring_input_timestamp_uses_only_rows_admitted_to_final_score(self) -> None:
        panel = pd.DataFrame(
            {"in_universe": [True, True, False]},
            index=pd.MultiIndex.from_tuples(
                [
                    (pd.Timestamp("2026-03-12"), "ETHUSDT"),
                    (pd.Timestamp("2026-03-12"), "SOLUSDT"),
                    (pd.Timestamp("2026-03-13"), "BTCUSDT"),
                ],
                names=["date", "symbol"],
            ),
        )

        timestamp = resolve_scoring_input_timestamp(panel, pd.Series([True, True, False], index=panel.index))

        self.assertEqual(timestamp, pd.Timestamp("2026-03-12"))


if __name__ == "__main__":
    unittest.main()
