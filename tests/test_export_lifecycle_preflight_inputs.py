from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.export_lifecycle_preflight_inputs import export_lifecycle_inputs


def test_export_lifecycle_inputs_writes_real_panel_contract(tmp_path: Path) -> None:
    dates = pd.date_range(end=pd.Timestamp.now().normalize(), periods=1000, freq="D")
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    panel = pd.DataFrame(index=index)
    panel["in_universe"] = True
    panel["open"] = range(1, len(panel) + 1)
    panel["close"] = panel["open"] + 0.5
    panel["final_score"] = 0.75

    manifest = export_lifecycle_inputs(panel, tmp_path)

    exported_panel = pd.read_csv(tmp_path / "research_panel.csv.gz")
    market_history = pd.read_csv(tmp_path / "market_history.csv.gz")
    persisted_manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert set(exported_panel.columns) == {"date", "symbol", "in_universe", "open", "final_score"}
    assert set(market_history["symbol"]) == {"BTCUSDT", "ETHUSDT"}
    assert manifest == persisted_manifest
    assert manifest["contract_version"] == "crypto.lifecycle_preflight.v1"
