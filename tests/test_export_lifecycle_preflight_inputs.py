from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.export_lifecycle_preflight_inputs import export_lifecycle_inputs


class ExportLifecyclePreflightInputsTests(unittest.TestCase):
    @staticmethod
    def _valid_panel() -> pd.DataFrame:
        dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=1000, freq="D")
        symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
        panel = pd.DataFrame(index=index)
        panel["in_universe"] = True
        panel["open"] = range(1, len(panel) + 1)
        panel["close"] = panel["open"] + 0.5
        panel["final_score"] = 0.75
        return panel

    def test_export_lifecycle_inputs_writes_real_panel_contract(self) -> None:
        panel = self._valid_panel()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            manifest = export_lifecycle_inputs(panel, output_dir)

            exported_panel = pd.read_csv(output_dir / "research_panel.csv.gz")
            market_history = pd.read_csv(output_dir / "market_history.csv.gz")
            persisted_manifest = json.loads((output_dir / "manifest.json").read_text())

        self.assertEqual(
            set(exported_panel.columns),
            {"date", "symbol", "in_universe", "open", "final_score"},
        )
        self.assertEqual(set(market_history["symbol"]), {"BTCUSDT", "ETHUSDT"})
        self.assertEqual(manifest, persisted_manifest)
        self.assertEqual(manifest["contract_version"], "crypto.lifecycle_preflight.v2")
        self.assertEqual(manifest["domain"], "crypto")
        self.assertIn("market_start_date", manifest)
        self.assertIn("market_end_date", manifest)
        self.assertEqual(manifest["strategy_profile"], "crypto_live_pool_rotation")
        self.assertEqual(manifest["producer"], "export_lifecycle_preflight_inputs.py")

    def test_export_rejects_scored_date_without_universe(self) -> None:
        panel = self._valid_panel()
        latest_completed_date = panel.index.get_level_values("date").unique()[-3]
        panel.loc[(latest_completed_date, slice(None)), "in_universe"] = False

        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaisesRegex(
            ValueError,
            "at least two in-universe symbols per scored date",
        ):
            export_lifecycle_inputs(panel, Path(tmpdir))

    def test_export_preserves_open_rows_without_scores(self) -> None:
        panel = self._valid_panel()
        date = panel.index.get_level_values("date").unique()[500]
        panel.loc[(date, "BTCUSDT"), ["in_universe", "final_score"]] = [False, pd.NA]

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            export_lifecycle_inputs(panel, output_dir)
            exported = pd.read_csv(output_dir / "research_panel.csv.gz")

        row = exported.loc[
            (exported["date"] == date.date().isoformat())
            & (exported["symbol"] == "BTCUSDT")
        ]
        self.assertEqual(len(row), 1)
        self.assertTrue(pd.isna(row.iloc[0]["final_score"]))

    def test_export_rejects_stale_combo_history_even_when_panel_is_fresh(self) -> None:
        panel = self._valid_panel()
        cutoff = panel.index.get_level_values("date").max() - pd.Timedelta(days=10)
        combo_mask = panel.index.get_level_values("symbol").isin({"BTCUSDT", "ETHUSDT"})
        stale_mask = combo_mask & (panel.index.get_level_values("date") > cutoff)
        panel.loc[stale_mask, "close"] = pd.NA

        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaisesRegex(
            ValueError,
            "BTC/ETH market history is stale",
        ):
            export_lifecycle_inputs(panel, Path(tmpdir))
