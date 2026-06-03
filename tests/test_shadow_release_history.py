from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.shadow import build_shadow_release_history, summarize_shadow_release_history


class ShadowReleaseHistoryTests(unittest.TestCase):
    def test_build_shadow_release_history_writes_index_and_selection_meta(self) -> None:
        dates = [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")]
        symbols = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
        index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
        panel = pd.DataFrame(index=index)
        panel["in_universe"] = True
        panel["final_score"] = [
            0.9, 0.8, 0.2,
            0.3, 0.95, 0.7,
        ]
        panel["confidence"] = [
            0.7, 0.6, 0.1,
            0.4, 0.8, 0.5,
        ]
        panel["regime"] = [
            "broad_alt_strength", "broad_alt_strength", "broad_alt_strength",
            "risk_off", "risk_off", "risk_off",
        ]
        panel["regime_confidence"] = [
            0.8, 0.8, 0.8,
            0.6, 0.6, 0.6,
        ]

        metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "base_asset": ["AAA", "BBB", "CCC"],
            }
        )
        config = {
            "export": {"live_pool_size": 2},
            "publish": {"mode": "core_major", "source_project": "crypto-live-pool-pipelines"},
            "universe": {"live_mode": "core_major"},
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            index_table = build_shadow_release_history(
                panel=panel,
                metadata=metadata,
                config=config,
                output_dir=output_dir,
                activation_lag_days=1,
                selection_meta_fields=["final_score", "confidence"],
                profile_name="challenger_topk_60",
                source_track="shadow_candidate",
                candidate_status="shadow_candidate",
            )
            summary = summarize_shadow_release_history(index_table)

            self.assertEqual(len(index_table), 2)
            self.assertTrue((output_dir / "release_index.csv").exists())
            self.assertTrue((output_dir / "2024-01-31-core_major" / "live_pool.json").exists())
            self.assertEqual(index_table.iloc[0]["activation_date"], "2024-02-29")
            self.assertTrue(bool(index_table.iloc[0]["has_selection_meta"]))
            self.assertEqual(index_table.iloc[0]["symbols"], "AAAUSDT|BBBUSDT")
            self.assertEqual(index_table.iloc[0]["profile"], "challenger_topk_60")
            self.assertEqual(index_table.iloc[0]["source_track"], "shadow_candidate")
            self.assertEqual(index_table.iloc[0]["candidate_status"], "shadow_candidate")
            self.assertEqual(index_table.iloc[0]["regime"], "broad_alt_strength")
            self.assertAlmostEqual(float(index_table.iloc[1]["regime_confidence"]), 0.6)
            self.assertEqual(int(summary.iloc[0]["release_count"]), 2)

            with (output_dir / "2024-01-31-core_major" / "release_manifest.json").open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            with (output_dir / "2024-01-31-core_major" / "live_pool.json").open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["profile"], "challenger_topk_60")
            self.assertEqual(payload["source_track"], "shadow_candidate")
            self.assertEqual(payload["candidate_status"], "shadow_candidate")
            self.assertEqual(payload["expected_pool_size"], 2)
            self.assertEqual(manifest["regime"], "broad_alt_strength")
            self.assertAlmostEqual(float(manifest["regime_confidence"]), 0.8)


if __name__ == "__main__":
    unittest.main()
