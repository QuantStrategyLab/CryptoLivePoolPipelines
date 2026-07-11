from __future__ import annotations

import sys
import tempfile
import unittest
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.strategy_lifecycle.orchestrator_runner import (  # noqa: E402
    PROFILE_NAME,
    SUPPORTED_PROFILES,
    CryptoLivePoolBacktestRunner,
)


class CryptoOrchestratorRunnerTests(unittest.TestCase):
    @staticmethod
    def _write_bundle(root: Path, *, version: str = "v1", days: int = 900, age_days: int = 0, non_finite: bool = False) -> None:
        from src.strategy_lifecycle.orchestrator_runner import _synthetic_panel

        panel = _synthetic_panel(days=days).reset_index()
        panel["date"] = pd.to_datetime(panel["date"])
        panel["date"] += pd.Timestamp.today().normalize() - pd.Timedelta(days=age_days) - panel["date"].max()
        if non_finite:
            panel.loc[0, "open"] = float("inf")
        panel.to_csv(root / "research_panel.csv.gz", index=False, compression="gzip")
        market = panel[["date", "symbol", "open"]].rename(columns={"open": "close"})
        market.to_csv(root / "market_history.csv.gz", index=False, compression="gzip")
        manifest = {"contract_version": f"crypto.lifecycle_preflight.{version}"}
        if version == "v2":
            manifest.update({
                "domain": "crypto", "producer": "export_lifecycle_preflight_inputs.py",
                "strategy_profile": PROFILE_NAME, "panel_rows": len(panel),
                "panel_symbols": sorted(panel["symbol"].unique().tolist()),
                "market_rows": len(market), "market_symbols": sorted(market["symbol"].unique().tolist()),
                "start_date": panel["date"].min().date().isoformat(),
                "end_date": panel["date"].max().date().isoformat(),
                "market_start_date": market["date"].min().date().isoformat(),
                "market_end_date": market["date"].max().date().isoformat(),
            })
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_production_wrapper_requires_real_panel(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        self.assertIsNotNone(build_backtest_runner)
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError

        old = os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
        try:
            runner = build_backtest_runner()
            with self.assertRaises(InsufficientEvidenceError):
                runner.run(PROFILE_NAME, {})
        finally:
            if old is not None:
                os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_no_arg_factory_ignores_legacy_preflight_env(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, build_backtest_runner

        old = os.environ.get("PREFLIGHT_BUNDLE_ROOT")
        os.environ["PREFLIGHT_BUNDLE_ROOT"] = "/tmp/unrelated-preflight-bundle"
        os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
        try:
            with self.assertRaises(InsufficientEvidenceError):
                build_backtest_runner().run(PROFILE_NAME, {})
        finally:
            if old is None:
                os.environ.pop("PREFLIGHT_BUNDLE_ROOT", None)
            else:
                os.environ["PREFLIGHT_BUNDLE_ROOT"] = old

    def test_no_arg_factory_loads_valid_preflight_bundle(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root)
            old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
            os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
            try:
                runner = build_backtest_runner()
                self.assertIsNotNone(runner)
                self.assertEqual(runner._runner, None)
                result = runner.run(PROFILE_NAME, {})
                self.assertEqual(result.strategy_profile, PROFILE_NAME)
                self.assertIsNotNone(runner._runner)
            finally:
                if old is None:
                    os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
                else:
                    os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_v1_bundle_rejects_inconsistent_optional_date_metadata(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, load_preflight_panel

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["start_date"] = "2000-01-01"
            manifest["end_date"] = "2000-01-02"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(InsufficientEvidenceError):
                load_preflight_panel(root)

    def test_v2_manifest_is_strictly_loaded(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import build_backtest_runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root, version="v2")
            old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
            os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
            try:
                self.assertEqual(build_backtest_runner().run(PROFILE_NAME, {}).strategy_profile, PROFILE_NAME)
            finally:
                if old is None:
                    os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
                else:
                    os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_unknown_stale_and_non_finite_bundles_fail_closed(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, build_backtest_runner

        for kwargs in ({"version": "v3"}, {"age_days": 10}, {"non_finite": True}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_bundle(root, **kwargs)
                old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
                os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
                try:
                    with self.assertRaises(InsufficientEvidenceError):
                        build_backtest_runner().run(PROFILE_NAME, {})
                finally:
                    if old is None:
                        os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
                    else:
                        os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_malformed_manifest_and_future_bundle_fail_closed(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, build_backtest_runner

        for malformed in (True, False):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_bundle(root, age_days=-1 if not malformed else 0)
                if malformed:
                    (root / "manifest.json").write_text("[]", encoding="utf-8")
                old = os.environ.get("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT")
                os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = str(root)
                try:
                    with self.assertRaises(InsufficientEvidenceError):
                        build_backtest_runner().run(PROFILE_NAME, {})
                finally:
                    if old is None:
                        os.environ.pop("CRYPTO_LIFECYCLE_PREFLIGHT_ROOT", None)
                    else:
                        os.environ["CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"] = old

    def test_malformed_manifest_symbol_lists_fail_closed(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, load_preflight_panel

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root, version="v2")
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["panel_symbols"] = None
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(InsufficientEvidenceError):
                load_preflight_panel(root)

    def test_undersized_bundle_fails_closed(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, load_preflight_panel

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root, version="v2", days=100)
            with self.assertRaises(InsufficientEvidenceError):
                load_preflight_panel(root)

    def test_stale_market_history_fails_closed(self) -> None:
        from src.strategy_lifecycle.backtest_wrapper import InsufficientEvidenceError, load_preflight_panel

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_bundle(root, version="v2")
            market_path = root / "market_history.csv.gz"
            market = pd.read_csv(market_path, compression="gzip")
            market["date"] = pd.to_datetime(market["date"]) - pd.Timedelta(days=10)
            market.to_csv(market_path, index=False, compression="gzip")
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["market_start_date"] = market["date"].min().date().isoformat()
            manifest["market_end_date"] = market["date"].max().date().isoformat()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(InsufficientEvidenceError):
                load_preflight_panel(root)

    def test_supported_profile(self) -> None:
        self.assertIn(PROFILE_NAME, SUPPORTED_PROFILES)

    def test_run_returns_backtest_result(self) -> None:
        runner = CryptoLivePoolBacktestRunner(synthetic_days=1600)
        result = runner.run(
            PROFILE_NAME,
            {},
            start_date=date(2023, 6, 1),
            end_date=date(2024, 3, 1),
        )
        self.assertEqual(result.strategy_profile, PROFILE_NAME)
        self.assertEqual(result.domain, "crypto")
        self.assertIsNotNone(result.sharpe_ratio)

    def test_walk_forward_produces_one_result_per_window(self) -> None:
        from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
        from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore

        with tempfile.TemporaryDirectory() as tmp:
            store = PerformanceStore(local_root=Path(tmp))
            orchestrator = BacktestOrchestrator(store=store)
            orchestrator.register_runner("crypto", CryptoLivePoolBacktestRunner(synthetic_days=1600))
            windows = (
                (date(2023, 6, 1), date(2023, 12, 31)),
                (date(2024, 1, 1), date(2024, 6, 30)),
            )
            results = orchestrator.walk_forward(
                PROFILE_NAME,
                domain="crypto",
                params={},
                windows=windows,
            )
            self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
