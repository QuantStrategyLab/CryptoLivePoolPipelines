from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_monthly_review_briefing.py"
SPEC = importlib.util.spec_from_file_location("monthly_review_briefing", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MonthlyReviewBriefingTests(unittest.TestCase):
    def write_fixture_files(
        self,
        root: Path,
        *,
        challenger_last_as_of_date: str = "2026-03-13",
        include_shadow_outputs: bool = True,
    ) -> Path:
        output_dir = root / "data" / "output"
        shadow_dir = output_dir / "shadow_candidate_tracks"
        shadow_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "as_of_date": "2026-03-13",
            "official_baseline": {
                "profile": "baseline_blended_rank",
                "version": "2026-03-13-core_major",
                "mode": "core_major",
                "pool_size": 5,
            },
        }
        live_pool = {
            "as_of_date": "2026-03-13",
            "version": "2026-03-13-core_major",
            "mode": "core_major",
            "pool_size": 5,
            "symbols": ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "SOLUSDT"],
            "source_project": "crypto-leader-rotation",
        }
        manifest = {
            "as_of_date": "2026-03-13",
            "version": "2026-03-13-core_major",
            "mode": "core_major",
            "dry_run": True,
            "publish_enabled": False,
            "release_prefix": "crypto-leader-rotation/releases/2026-03-13-core_major",
            "current_prefix": "crypto-leader-rotation/current",
            "firestore": {
                "collection": "strategy",
                "document": "CRYPTO_LEADER_ROTATION_LIVE_POOL",
            },
        }
        with (output_dir / "live_pool.json").open("w", encoding="utf-8") as handle:
            json.dump(live_pool, handle)
        with (output_dir / "release_manifest.json").open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        with (output_dir / "release_status_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "official_release": {
                        "as_of_date": "2026-03-13",
                        "version": "2026-03-13-core_major",
                        "mode": "core_major",
                        "pool_size": 5,
                        "symbols": ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "SOLUSDT"],
                        "source_project": "crypto-leader-rotation",
                    },
                    "validation": {"errors": [], "warnings": []},
                },
                handle,
            )
        if include_shadow_outputs:
            with (output_dir / "monthly_shadow_build_summary.json").open("w", encoding="utf-8") as handle:
                json.dump(summary, handle)
            with (shadow_dir / "track_summary.csv").open("w", encoding="utf-8") as handle:
                handle.write(
                    "track_id,profile_name,target_mode,source_track,candidate_status,release_count,first_as_of_date,last_as_of_date,release_index_path\n"
                    "official_baseline,baseline_blended_rank,blended_rank_pct,official_baseline,official_reference,64,2020-12-31,2026-03-13,official/release_index.csv\n"
                    f"challenger_topk_60,challenger_topk_60,future_topk_label_60,shadow_candidate,shadow_candidate,64,2020-12-31,{challenger_last_as_of_date},challenger/release_index.csv\n"
                )
            (shadow_dir / "official").mkdir()
            (shadow_dir / "official" / "release_index.csv").write_text(
                "version,as_of_date,pool_size,symbols,pool_stability,pool_churn\n"
                "2026-03-13-core_major,2026-03-13,5,TRXUSDT|ETHUSDT|BCHUSDT|NEARUSDT|SOLUSDT,0.8,0.2\n",
                encoding="utf-8",
            )
            (shadow_dir / "challenger").mkdir()
            (shadow_dir / "challenger" / "release_index.csv").write_text(
                "version,as_of_date,pool_size,symbols,pool_stability,pool_churn\n"
                "2026-03-13-core_major,2026-03-13,5,TRXUSDT|ETHUSDT|BCHUSDT|XRPUSDT|DOGEUSDT,0.6,0.4\n",
                encoding="utf-8",
            )
        (output_dir / "latest_ranking.csv").write_text(
            "as_of_date,symbol,final_score,confidence,liquidity_stability,avg_quote_vol_180,selected_flag,current_rank\n"
            "2026-03-13,TRXUSDT,0.90,0.70,0.90,1000,true,1\n"
            "2026-03-13,ETHUSDT,0.80,0.60,0.80,900,true,2\n"
            "2026-03-13,BCHUSDT,0.70,0.50,0.70,800,true,3\n"
            "2026-03-13,NEARUSDT,0.60,0.40,0.60,700,true,4\n"
            "2026-03-13,SOLUSDT,0.50,0.30,0.50,600,true,5\n"
            "2026-03-13,XRPUSDT,0.49,0.20,0.40,500,false,6\n",
            encoding="utf-8",
        )
        return output_dir

    def test_build_review_payload_reports_ok_when_outputs_align(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir))
            inputs = MODULE.build_review_inputs(output_dir)
            payload = MODULE.build_review_payload(inputs)
            outputs = MODULE.write_outputs(payload, output_dir)

            self.assertTrue(outputs["review_markdown"].exists())
            self.assertTrue(outputs["review_json"].exists())
            self.assertTrue(outputs["review_prompt"].exists())

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["official_baseline"]["pool_size"], 5)
        self.assertEqual(payload["tracks"]["challenger_topk_60"]["release_count"], 64)
        self.assertEqual(
            payload["track_release_previews"]["challenger_topk_60"]["symbols"],
            ["TRXUSDT", "ETHUSDT", "BCHUSDT", "XRPUSDT", "DOGEUSDT"],
        )
        self.assertEqual(payload["track_release_previews"]["challenger_topk_60"]["overlap_with_official"], 3)
        self.assertEqual(payload["selection_boundary"]["next_candidate"]["symbol"], "XRPUSDT")
        self.assertAlmostEqual(payload["selection_boundary"]["score_gap_to_next"], 0.01)
        self.assertEqual(payload["warnings"], [])

    def test_build_review_payload_warns_when_track_dates_do_not_align(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir), challenger_last_as_of_date="2026-02-13")
            inputs = MODULE.build_review_inputs(output_dir)
            payload = MODULE.build_review_payload(inputs)
            review_md = MODULE.render_review_markdown(payload)
            prompt_md = MODULE.render_review_prompt(payload)

        self.assertEqual(payload["status"], "warning")
        self.assertIn("challenger_topk_60 last_as_of_date does not match monthly summary", payload["warnings"])
        self.assertIn("## Warnings", review_md)
        self.assertIn("official_baseline remains the production reference", prompt_md)

    def test_build_review_payload_requires_shadow_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir), include_shadow_outputs=False)
            inputs = MODULE.build_review_inputs(output_dir)
            with self.assertRaisesRegex(RuntimeError, "monthly shadow build outputs are required"):
                MODULE.require_shadow_outputs(inputs)

    def test_build_review_inputs_rejects_empty_track_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = self.write_fixture_files(Path(tmp_dir))
            track_summary = output_dir / "shadow_candidate_tracks" / "track_summary.csv"
            track_summary.write_text(
                "track_id,profile_name,target_mode,source_track,candidate_status,release_count,first_as_of_date,last_as_of_date,release_index_path\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "CSV is empty"):
                MODULE.build_review_inputs(output_dir)


if __name__ == "__main__":
    unittest.main()
