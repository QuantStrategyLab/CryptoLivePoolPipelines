from __future__ import annotations

import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.indicators import compute_btc_cycle_indicators
from src.pipeline import _export_btc_cycle_from_history, build_live_pool_outputs
from src.publish import upload_release_artifacts


class BtcCycleExportTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range("2025-01-01", periods=260, freq="D")
        self.close = pd.Series(range(10000, 10260), index=self.dates, dtype=float)

    def test_csv_to_actual_export_uses_named_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = pd.DataFrame({"date": self.dates, "symbol": "BTCUSDT", "open": 9.0,
                                    "close": self.close.to_numpy()})
            history.to_csv(root / "BTCUSDT.csv", index=False)
            result = _export_btc_cycle_from_history(root / "BTCUSDT.csv", root, self.dates[-1])
            exported = json.loads((root / "btc_cycle_indicators.json").read_text())
            self.assertEqual(exported, result)
            self.assertEqual(result["close"], self.close.iloc[-1])
            self.assertGreater(result["ahr999"], 0)
            self.assertAlmostEqual(result["high252"], self.close.iloc[-252:].max())

    def test_bad_close_is_rejected(self):
        for values in ({"other": [10]}, {"close": ["bad"]}, {"close": [float("inf")]},
                       {"close": [0]}):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pd.DataFrame({"date": ["2026-01-01"], "symbol": ["BTCUSDT"], **values}).to_csv(root / "BTCUSDT.csv", index=False)
                with self.assertRaises(ValueError):
                    _export_btc_cycle_from_history(root / "BTCUSDT.csv", root, pd.Timestamp("2026-01-01"))
                self.assertFalse((root / "btc_cycle_indicators.json").exists())

    def test_date_column_order_and_future_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = pd.DataFrame({"symbol": "BTCUSDT", "date": self.dates, "close": self.close.to_numpy()})
            path = root / "BTCUSDT.csv"
            history.to_csv(path, index=False)
            baseline = _export_btc_cycle_from_history(path, root, self.dates[-1])
            future = pd.DataFrame({"symbol": ["BTCUSDT", "BTCUSDT"],
                                   "date": [self.dates[-1] + pd.Timedelta(days=1)] * 2,
                                   "close": ["bad", float("nan")]})
            pd.concat([history, future], ignore_index=True).to_csv(path, index=False)
            self.assertEqual(_export_btc_cycle_from_history(path, root, self.dates[-1]), baseline)

    def test_bad_date_or_selected_wrong_symbol_is_rejected(self):
        for date, symbol in (("unparseable", "BTCUSDT"), ("2026-01-01", "ETHUSDT")):
            with self.subTest(date=date, symbol=symbol), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pd.DataFrame({"date": [date], "symbol": [symbol], "close": [100]}).to_csv(root / "BTCUSDT.csv", index=False)
                with self.assertRaises(ValueError):
                    _export_btc_cycle_from_history(root / "BTCUSDT.csv", root, pd.Timestamp("2026-01-01"))

    def test_strict_cutoff_checks_recorded_receipt_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            times = self.dates.tz_localize("UTC").as_unit("ns").asi8 // 1000000
            history = pd.DataFrame({"date": self.dates, "symbol": "BTCUSDT", "close": self.close.to_numpy(),
                                    "open_time": times, "close_time": times + 86400000 - 1,
                                    "received_at": times + 86400000 + 1000})
            path = root / "BTCUSDT.csv"
            history.to_csv(path, index=False)
            cutoff = self.dates[-1].tz_localize("UTC") + pd.Timedelta(days=1, seconds=2)
            out = _export_btc_cycle_from_history(path, root, self.dates[-1], available_by=cutoff)
            self.assertEqual(out["close"], self.close.iloc[-1])
            with self.assertRaisesRegex(ValueError, "timing"):
                _export_btc_cycle_from_history(path, root, self.dates[-1], available_by=cutoff - pd.Timedelta(seconds=2))
            history.loc[0, "received_at"] = None
            history.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "timing"):
                _export_btc_cycle_from_history(path, root, self.dates[-1], available_by=cutoff)
            with self.assertRaisesRegex(ValueError, "UTC"):
                _export_btc_cycle_from_history(path, root, self.dates[-1], available_by=pd.Timestamp("2026-01-01"))

    def test_strict_rejects_missing_daily_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dates = self.dates.delete(100)
            times = dates.tz_localize("UTC").as_unit("ns").asi8 // 1000000
            pd.DataFrame({"date": dates, "symbol": "BTCUSDT", "close": 100.0,
                          "open_time": times, "close_time": times + 86400000 - 1,
                          "received_at": times + 86400000 + 1000}).to_csv(root / "BTCUSDT.csv", index=False)
            with self.assertRaisesRegex(ValueError, "continuous"):
                _export_btc_cycle_from_history(root / "BTCUSDT.csv", root, self.dates[-1],
                                               available_by=pd.Timestamp("2026-01-01T00:00:00Z"))

    def test_strict_accepts_continuous_dates_at_microsecond_and_nanosecond_precision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            times = self.dates.tz_localize("UTC").as_unit("ns").asi8 // 1000000
            history = pd.DataFrame({"date": self.dates, "symbol": "BTCUSDT", "close": self.close.to_numpy(),
                                    "open_time": times, "close_time": times + 86400000 - 1,
                                    "received_at": times + 86400000 + 1000})
            path = root / "BTCUSDT.csv"
            history.to_csv(path, index=False)
            parse_dates = pd.to_datetime
            for unit in ("us", "ns"):
                def with_precision(*args, **kwargs):
                    result = parse_dates(*args, **kwargs)
                    if isinstance(result, pd.Series):
                        return result.dt.as_unit(unit)
                    return result
                with self.subTest(unit=unit), patch("src.pipeline.pd.to_datetime", side_effect=with_precision):
                    out = _export_btc_cycle_from_history(path, root, self.dates[-1],
                        available_by=self.dates[-1].tz_localize("UTC") + pd.Timedelta(days=1, seconds=2))
                    self.assertEqual(out["close"], self.close.iloc[-1])

    def test_non_btc_benchmark_build_never_uses_its_history_as_btc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame({"date": ["2026-01-01"], "symbol": ["ETHUSDT"], "close": [100]}).to_csv(root / "ETHUSDT.csv", index=False)
            panel = pd.DataFrame({"in_universe": [True], "blended_target": [1]}, index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-01-01"), "ETHUSDT")], names=["date", "symbol"]))
            model = types.SimpleNamespace(predictions=pd.DataFrame(), model_run_manifest={}, linear_backend="test", ml_backend="test")
            snapshot = pd.DataFrame({"selected_flag": [True], "in_universe": [True]})
            config = {"paths": types.SimpleNamespace(raw_dir=root, output_dir=root),
                      "data": {"benchmark_symbol": "ETHUSDT"}, "export": {"save_legacy_live_pool": True, "live_pool_size": 1},
                      "labels": {"horizons": [1]}, "walkforward": {"train_window_days": 1}}
            with patch.multiple("src.pipeline", resolve_clean_source_revision=lambda: "a" * 40,
                resolve_universe_mode=lambda *a, **k: ("test", {}), prepare_research_panel=lambda *a, **k: (panel, {}),
                fit_predict_models=lambda *a, **k: model, build_final_scores=lambda p, c: p,
                export_latest_universe=lambda *a, **k: None, export_latest_ranking=lambda *a, **k: None,
                latest_ranking_snapshot=lambda *a, **k: snapshot, export_live_pool=lambda **k: {},
                export_strategy_artifact_manifest=lambda **k: {}):
                result = build_live_pool_outputs(config)
            self.assertEqual(result["btc_cycle_indicators"], {})
            self.assertFalse((root / "btc_cycle_indicators.json").exists())

    def test_build_retires_previous_cycle_before_early_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "btc_cycle_indicators.json"
            current.write_bytes(b"old successful bytes")
            config = {"paths": types.SimpleNamespace(output_dir=root), "export": {}}
            with patch("src.pipeline.resolve_clean_source_revision", side_effect=RuntimeError("build failed")):
                with self.assertRaisesRegex(RuntimeError, "build failed"):
                    build_live_pool_outputs(config)
            self.assertFalse(current.exists())
            archived = list((root / "btc-cycle-history").glob("*/btc_cycle_indicators.json"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_bytes(), b"old successful bytes")

    def test_retirement_runs_before_real_clean_source_check(self):
        for tracked in (False, True):
            with self.subTest(tracked=tracked), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                output = root / "data" / "output"
                output.mkdir(parents=True)
                current = output / "btc_cycle_indicators.json"
                current.write_bytes(b"previous cycle")
                (root / ".gitignore").write_text("data/output/*\n!data/output/.gitkeep\n")
                subprocess.run(["git", "init", "-q"], cwd=root, check=True)
                subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
                if tracked:
                    subprocess.run(["git", "add", "-f", str(current.relative_to(root))], cwd=root, check=True)
                subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                "commit", "-qm", "fixture"], cwd=root, check=True)
                config = {"paths": types.SimpleNamespace(output_dir=output), "export": {}}
                # Use the production resolver unchanged, bound to this temporary repository.
                with patch("src.export.__file__", str(root / "src" / "export.py")), patch(
                    "src.pipeline.resolve_universe_mode", side_effect=LookupError("past clean check")
                ) as next_step:
                    if tracked:
                        with self.assertRaisesRegex(RuntimeError, "clean producer commit"):
                            build_live_pool_outputs(config)
                        next_step.assert_not_called()
                    else:
                        with self.assertRaisesRegex(LookupError, "past clean check"):
                            build_live_pool_outputs(config)
                        next_step.assert_called_once()
                self.assertFalse(current.exists())
                archived = list((output / "btc-cycle-history").glob("*/btc_cycle_indicators.json"))
                self.assertEqual([path.read_bytes() for path in archived], [b"previous cycle"])

    def test_retirement_failure_aborts_without_deleting_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "btc_cycle_indicators.json"
            current.write_bytes(b"old successful bytes")
            config = {"paths": types.SimpleNamespace(output_dir=root), "export": {}}
            with patch.object(Path, "replace", side_effect=OSError("archive failed")), patch(
                "src.pipeline.resolve_clean_source_revision"
            ) as resolve:
                with self.assertRaisesRegex(OSError, "archive failed"):
                    build_live_pool_outputs(config)
            resolve.assert_not_called()
            self.assertEqual(current.read_bytes(), b"old successful bytes")

    def test_current_cycle_file_tracks_this_build_outcome(self):
        for outcome in ("missing", "failed", "success"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                current = root / "btc_cycle_indicators.json"
                current.write_bytes(b"old successful bytes")
                if outcome != "missing":
                    (root / "BTCUSDT.csv").write_text("synthetic")
                panel = pd.DataFrame({"in_universe": [True], "blended_target": [1]}, index=pd.MultiIndex.from_tuples(
                    [(pd.Timestamp("2026-01-01"), "BTCUSDT")], names=["date", "symbol"]))
                model = types.SimpleNamespace(predictions=pd.DataFrame(), model_run_manifest={}, linear_backend="test", ml_backend="test")
                snapshot = pd.DataFrame({"selected_flag": [True], "in_universe": [True]})
                config = {"paths": types.SimpleNamespace(raw_dir=root, output_dir=root),
                          "export": {"save_legacy_live_pool": True, "live_pool_size": 1},
                          "labels": {"horizons": [1]}, "walkforward": {"train_window_days": 1}}
                def export(*args, **kwargs):
                    (Path(args[1]) / current.name).write_text("partial" if outcome == "failed" else "new result")
                    if outcome == "failed":
                        raise ValueError("invalid BTC data")
                    return {"ahr999": 2.0}
                with patch.multiple("src.pipeline", resolve_clean_source_revision=lambda: "a" * 40,
                    resolve_universe_mode=lambda *a, **k: ("test", {}), prepare_research_panel=lambda *a, **k: (panel, {}),
                    fit_predict_models=lambda *a, **k: model, build_final_scores=lambda p, c: p,
                    export_latest_universe=lambda *a, **k: None, export_latest_ranking=lambda *a, **k: None,
                    latest_ranking_snapshot=lambda *a, **k: snapshot, export_live_pool=lambda **k: {},
                    export_strategy_artifact_manifest=lambda **k: {}, _export_btc_cycle_from_history=export):
                    result = build_live_pool_outputs(config)
                archived = list((root / "btc-cycle-history").glob("*/btc_cycle_indicators.json"))
                self.assertEqual([p.read_bytes() for p in archived], [b"old successful bytes"])
                self.assertEqual(bool(result["btc_cycle_indicators"]), outcome == "success")
                self.assertEqual(current.exists(), outcome == "success")
                if outcome == "success":
                    self.assertEqual(current.read_text(), "new result")

    def test_future_rows_do_not_change_any_cutoff_metric(self):
        baseline = compute_btc_cycle_indicators(self.close, as_of_date=self.dates[-1])
        with_future = pd.concat([self.close, pd.Series([1e12], index=[self.dates[-1] + pd.Timedelta(days=1)])])
        self.assertEqual(compute_btc_cycle_indicators(with_future, as_of_date=self.dates[-1]), baseline)

    def test_cutoff_before_first_row_rejected(self):
        with self.assertRaises(ValueError):
            compute_btc_cycle_indicators(self.close, as_of_date=self.dates[0] - pd.Timedelta(days=1))

    def test_optional_old_cycle_file_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ["latest_universe.json", "latest_ranking.csv", "live_pool.json", "live_pool_legacy.json",
                     "model_run_manifest.json", "artifact_manifest.json"]
            paths = {name: root / name for name in names}
            for path in paths.values():
                path.write_text("synthetic")
            (root / "btc_cycle_indicators.json").write_text("old synthetic cycle")
            artifacts = types.SimpleNamespace(output_dir=root, **{
                name.replace(".json", "").replace(".csv", "") + "_path": path
                for name, path in paths.items()
            })
            settings = types.SimpleNamespace(dry_run=False, project_id="test", cloud_bucket="test",
                                             upload_current_pointer=True)
            layout = {"objects": {name: {"release_uri": "release/" + name, "current_uri": "current/" + name}
                                  for name in names}}
            writes = []
            cloud = types.ModuleType("quant_platform_kit.cloud")
            cloud.get_object_store = lambda: types.SimpleNamespace(write_bytes=lambda uri, data: writes.append(uri))
            package = types.ModuleType("quant_platform_kit")
            package.cloud = cloud
            with patch.dict("sys.modules", {"quant_platform_kit": package, "quant_platform_kit.cloud": cloud}):
                upload_release_artifacts(settings, artifacts, layout, live_pool_legacy_exact_bytes=b"synthetic")
            self.assertEqual(len(writes), 12)
            self.assertFalse(any("btc_cycle" in uri for uri in writes))
            self.assertEqual((root / "btc_cycle_indicators.json").read_text(), "old synthetic cycle")


if __name__ == "__main__":
    unittest.main()
