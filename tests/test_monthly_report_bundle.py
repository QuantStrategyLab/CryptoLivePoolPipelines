from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_monthly_report_bundle.py"
SPEC = importlib.util.spec_from_file_location("monthly_report_bundle", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MonthlyReportBundleTests(unittest.TestCase):
    def write_fixture_files(self, root: Path, *, include_strategy_metrics: bool = True) -> Path:
        output_dir = root / "data" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "release_status_summary.json").write_text(
            json.dumps(
                {
                    "official_release": {
                        "as_of_date": "2026-03-13",
                        "version": "2026-03-13-core_major",
                        "mode": "core_major",
                        "pool_size": 5,
                        "symbols": ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "SOLUSDT"],
                    },
                    "validation": {"errors": [], "warnings": []},
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "release_status_summary.md").write_text(
            "# Release Status Summary\n\nGenerated: fixture\n",
            encoding="utf-8",
        )
        (output_dir / "monthly_review.json").write_text(
            json.dumps({"as_of_date": "2026-03-13", "warnings": [], "status": "ok"}),
            encoding="utf-8",
        )
        (output_dir / "monthly_review.md").write_text(
            "# Monthly Review\n\n## Current release status\n",
            encoding="utf-8",
        )
        (output_dir / "monthly_review_prompt.md").write_text("Monthly release review prompt\n", encoding="utf-8")
        (output_dir / "monthly_telegram.txt").write_text("CryptoLivePoolPipelines monthly release\n", encoding="utf-8")
        if include_strategy_metrics:
            (output_dir / "strategy_metrics.json").write_text(
                json.dumps(
                    {
                        "schema_version": "strategy_performance.v2",
                        "metrics_kind": "performance",
                        "snapshots": [
                            {
                                "strategy_profile": "baseline_blended_rank",
                                "plugin": "official_baseline",
                                "current_metrics": {
                                    "sharpe": 0.8,
                                    "cagr": 0.2,
                                    "calmar": 0.5,
                                    "win_rate": 0.55,
                                    "max_dd": 0.4,
                                },
                                "baseline_metrics": {
                                    "sharpe": 0.7,
                                    "cagr": 0.18,
                                    "calmar": 0.45,
                                    "win_rate": 0.52,
                                    "max_dd": 0.42,
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return output_dir

    def test_write_bundle_copies_files_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir))
            bundle_dir = output_dir / "monthly_report_bundle"
            outputs = MODULE.write_bundle(output_dir, bundle_dir)
            manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["artifact_name"], "monthly-report-2026-03-13")
            self.assertEqual(manifest["report_month"], "2026-03")
            self.assertEqual(manifest["pool_size"], 5)
            self.assertIn("monthly_telegram.txt", manifest["artifact_files"])
            self.assertIn("strategy_metrics.json", manifest["artifact_files"])
            self.assertTrue((bundle_dir / "strategy_metrics.json").exists())
            self.assertTrue((bundle_dir / "ai_review_input.md").exists())
            self.assertTrue((bundle_dir / "job_summary.md").exists())
            ai_review_input = (bundle_dir / "ai_review_input.md").read_text(encoding="utf-8")
            self.assertIn("upstream selector review", ai_review_input)
            self.assertIn("Shadow / challenger coverage", ai_review_input)
            self.assertIn("Strategy review questions", ai_review_input)
            self.assertIn("## Release Status Summary\n\nGenerated: fixture", ai_review_input)
            self.assertIn("## Monthly Review\n\n## Current release status", ai_review_input)
            self.assertIn("## Strategy Metrics", ai_review_input)
            self.assertIn("baseline_blended_rank (official_baseline)", ai_review_input)
            self.assertIn("sharpe=0.800000", ai_review_input)
            self.assertIn("missing_current=none", ai_review_input)
            self.assertNotIn("## Release Status Summary\n\n# Release Status Summary", ai_review_input)
            self.assertNotIn("## Monthly Review\n\n# Monthly Review", ai_review_input)

    def test_write_bundle_keeps_strategy_metrics_optional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir), include_strategy_metrics=False)
            bundle_dir = output_dir / "monthly_report_bundle"
            outputs = MODULE.write_bundle(output_dir, bundle_dir)
            manifest = json.loads(outputs["manifest"].read_text(encoding="utf-8"))
            ai_review_input = outputs["ai_review_input"].read_text(encoding="utf-8")

        self.assertNotIn("strategy_metrics.json", manifest["artifact_files"])
        self.assertFalse((bundle_dir / "strategy_metrics.json").exists())
        self.assertIn("Strategy performance metrics were not generated", ai_review_input)


if __name__ == "__main__":
    unittest.main()
