from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_monthly_build_telegram.py"
SPEC = importlib.util.spec_from_file_location("monthly_build_telegram", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class MonthlyBuildTelegramTests(unittest.TestCase):
    def write_fixture_files(
        self,
        root: Path,
        *,
        include_manifest: bool = True,
        include_shadow_outputs: bool = True,
    ) -> None:
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
        }
        with (output_dir / "live_pool.json").open("w", encoding="utf-8") as handle:
            json.dump(live_pool, handle)
        with (output_dir / "release_status_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "official_release": {
                        "as_of_date": "2026-03-13",
                        "version": "2026-03-13-core_major",
                        "mode": "core_major",
                        "pool_size": 5,
                    },
                    "validation": {"ok": True, "manifest_present": include_manifest, "age_days": 1, "errors": [], "warnings": []},
                },
                handle,
            )
        if include_manifest:
            with (output_dir / "release_manifest.json").open("w", encoding="utf-8") as handle:
                json.dump({"dry_run": True, "version": "2026-03-13-core_major"}, handle)
        if include_shadow_outputs:
            with (output_dir / "monthly_shadow_build_summary.json").open("w", encoding="utf-8") as handle:
                json.dump(summary, handle)
            with (shadow_dir / "track_summary.csv").open("w", encoding="utf-8") as handle:
                handle.write(
                    "track_id,profile_name,target_mode,source_track,candidate_status,release_count,first_as_of_date,last_as_of_date,release_index_path\n"
                    "official_baseline,baseline_blended_rank,blended_rank_pct,official_baseline,official_reference,64,2020-12-31,2026-03-13,official/release_index.csv\n"
                    "challenger_topk_60,challenger_topk_60,future_topk_label_60,shadow_candidate,shadow_candidate,64,2020-12-31,2026-03-13,challenger/release_index.csv\n"
                )

    def test_build_health_payload_reports_ok_for_complete_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root)
            payload = MODULE.build_health_payload(root / "data" / "output")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["as_of_date"], "2026-03-13")
        self.assertTrue(payload["manifest"]["present"])
        self.assertEqual(payload["shadow_tracks"]["challenger_topk_60"]["release_count"], 64)
        self.assertEqual(payload["warnings"], [])

    def test_build_health_payload_warns_when_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root, include_manifest=False)
            payload = MODULE.build_health_payload(root / "data" / "output")
            message = MODULE.format_message(payload)

        self.assertEqual(payload["status"], "warning")
        self.assertIn("missing release_manifest.json", payload["warnings"])
        self.assertIn("status: warning", message)
        self.assertIn("challenger_topk_60", message)

    def test_build_health_payload_allows_release_status_only_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root, include_shadow_outputs=False)
            payload = MODULE.build_health_payload(root / "data" / "output")
            message = MODULE.format_message(payload)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["as_of_date"], "2026-03-13")
        self.assertFalse(payload["shadow_tracks"]["official_baseline"]["available"])
        self.assertIn("shadow: not_generated_in_this_run", message)

    def test_format_message_uses_chinese_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root)
            payload = MODULE.build_health_payload(root / "data" / "output")
            message = MODULE.format_message(payload, lang="zh")

        self.assertIn("CryptoLivePoolPipelines 月度发布", message)
        self.assertIn("状态: 正常", message)
        self.assertIn("官方池:", message)
        self.assertIn("策略配置=baseline_blended_rank", message)
        self.assertIn("影子池：官方基线最近日期=2026-03-13", message)
        self.assertIn("发布清单：已存在；演练模式=False", message)
        self.assertIn("校验：通过=True；清单存在=True；数据距今天数=1", message)
        self.assertIn("告警: 无", message)
        self.assertNotIn("profile=", message)
        self.assertNotIn("pool_size=", message)

    def test_chinese_warning_labels_preserve_raw_diagnostics_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root, include_manifest=False)
            payload = MODULE.build_health_payload(root / "data" / "output")
            message = MODULE.format_message(payload, lang="zh")

        self.assertIn("告警（原文）: missing release_manifest.json", message)

    def test_main_uses_global_telegram_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self.write_fixture_files(root)
            args = type(
                "Args",
                (),
                {
                    "output_dir": root / "data" / "output",
                    "output_path": None,
                    "print_only": False,
                },
            )()
            stdout = StringIO()
            with patch.object(MODULE, "parse_args", return_value=args), patch.object(
                MODULE, "send_telegram_message"
            ) as send_message, patch.dict(
                os.environ,
                {"TELEGRAM_BOT_TOKEN": "test-token", "GLOBAL_TELEGRAM_CHAT_ID": "shared-chat-id"},
                clear=True,
            ), redirect_stdout(stdout):
                MODULE.main()

        send_message.assert_called_once()
        self.assertEqual(send_message.call_args.args[0], "test-token")
        self.assertEqual(send_message.call_args.args[1], "shared-chat-id")
        self.assertIn("telegram_send=ok", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
