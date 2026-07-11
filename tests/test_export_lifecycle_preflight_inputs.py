from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

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


def test_export_rejects_scored_date_without_universe(tmp_path: Path) -> None:
    panel = _valid_panel()
    latest_date = panel.index.get_level_values("date").max()
    panel.loc[(latest_date, slice(None)), "in_universe"] = False

    with pytest.raises(ValueError, match="at least two in-universe symbols per scored date"):
        export_lifecycle_inputs(panel, tmp_path)


def test_export_rejects_stale_combo_history_even_when_panel_is_fresh(tmp_path: Path) -> None:
    panel = _valid_panel()
    cutoff = panel.index.get_level_values("date").max() - pd.Timedelta(days=10)
    combo_mask = panel.index.get_level_values("symbol").isin({"BTCUSDT", "ETHUSDT"})
    stale_mask = combo_mask & (panel.index.get_level_values("date") > cutoff)
    panel.loc[stale_mask, "close"] = pd.NA

    with pytest.raises(ValueError, match="BTC/ETH market history is stale"):
        export_lifecycle_inputs(panel, tmp_path)
