from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "generate_strategy_metrics.py"
SPEC = importlib.util.spec_from_file_location("generate_strategy_metrics", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GenerateStrategyMetricsTests(unittest.TestCase):
    def test_track_metrics_emits_canonical_watcher_metrics(self) -> None:
        table = pd.DataFrame(
            {
                "CAGR": [0.10, 0.20],
                "Sharpe": [0.8, 1.2],
                "Calmar": [0.7, 1.1],
                "Win Rate": [0.50, 0.60],
                "Max Drawdown": [-0.20, -0.10],
                "Turnover": [1.0, 2.0],
            }
        )

        metrics = MODULE._track_metrics(table)

        self.assertEqual(
            set(("sharpe", "cagr", "calmar", "win_rate", "max_dd", "turnover")),
            set(metrics["current_metrics"]),
        )
        self.assertEqual(metrics["current_metrics"]["max_dd"], 0.10)
        self.assertAlmostEqual(metrics["baseline_metrics"]["max_dd"], 0.15)
        self.assertEqual(metrics["baseline_metrics"]["sharpe"], 1.0)

    def test_track_metrics_prefers_canonical_name_on_collision(self) -> None:
        table = pd.DataFrame(
            {
                "Max Drawdown": [-0.20],
                "max_dd": [0.10],
            }
        )

        metrics = MODULE._track_metrics(table)

        self.assertEqual(metrics["current_metrics"]["max_dd"], 0.10)

    def test_generate_payload_is_versioned_and_marks_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            release_dir = root / "release" / "2026-07-11"
            release_dir.mkdir(parents=True)
            (release_dir.parent / "release_index.csv").write_text(
                "CAGR,Sharpe,Calmar,Win Rate,Max Drawdown\n"
                "0.10,0.8,0.7,0.50,-0.20\n"
                "0.20,1.2,1.1,0.60,-0.10\n",
                encoding="utf-8",
            )
            summary_path = root / "monthly_shadow_build_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "official_baseline": {
                            "profile": "baseline",
                            "live_pool_path": str(release_dir / "live_pool.json"),
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = MODULE.generate_strategy_metrics(summary_path, repo="QuantStrategyLab/Test")

        self.assertEqual(payload["schema_version"], "strategy_performance.v2")
        self.assertEqual(payload["metrics_kind"], "performance")
        self.assertEqual(len(payload["snapshots"]), 1)
        snapshot = payload["snapshots"][0]
        self.assertEqual(snapshot["schema_version"], "strategy_performance.v2")
        self.assertEqual(snapshot["metrics_kind"], "performance")
        self.assertEqual(set(("sharpe", "cagr", "calmar", "win_rate", "max_dd")), set(snapshot["current_metrics"]))

    def test_generate_payload_skips_missing_baseline_without_metrics_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            live_pool = root / "release" / "2026-07-11" / "live_pool.json"
            live_pool.parent.mkdir(parents=True)
            summary_path = root / "monthly_shadow_build_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "official_baseline": {
                            "profile": "baseline",
                            "live_pool_path": str(live_pool),
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = MODULE.generate_strategy_metrics(summary_path)

        self.assertEqual(payload["snapshots"], [])

    def test_generate_payload_marks_operational_indexes_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            release_dir = root / "release" / "2026-07-11"
            release_dir.mkdir(parents=True)
            (release_dir.parent / "release_index.csv").write_text(
                "pool_size,pool_churn\n5,0.2\n6,0.1\n",
                encoding="utf-8",
            )
            summary_path = root / "monthly_shadow_build_summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "official_baseline": {
                            "profile": "baseline",
                            "live_pool_path": str(release_dir / "live_pool.json"),
                        }
                    }
                ),
                encoding="utf-8",
            )

            payload = MODULE.generate_strategy_metrics(summary_path)

        self.assertEqual(payload["schema_version"], "strategy_operational_metrics.v1")
        self.assertEqual(payload["metrics_kind"], "operational_quality")
        self.assertEqual(payload["snapshots"][0]["metrics_kind"], "operational_quality")


if __name__ == "__main__":
    unittest.main()
