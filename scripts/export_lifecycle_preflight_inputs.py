#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.pipeline import run_research_pipeline

PANEL_COLUMNS = ("in_universe", "open", "final_score")
COMBO_SYMBOLS = ("BTCUSDT", "ETHUSDT")


def export_lifecycle_inputs(panel: pd.DataFrame, output_dir: Path) -> dict[str, object]:
    if list(panel.index.names) != ["date", "symbol"]:
        raise ValueError("research panel must use a date/symbol MultiIndex")
    missing = sorted(set((*PANEL_COLUMNS, "close")) - set(panel.columns))
    if missing:
        raise ValueError(f"research panel is missing columns: {', '.join(missing)}")

    frame = panel.reset_index().copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    lifecycle_panel = frame[["date", "symbol", *PANEL_COLUMNS]].dropna(subset=["date", "open", "final_score"])
    if lifecycle_panel.empty:
        raise ValueError("research panel has no scored lifecycle rows")

    market_history = frame.loc[frame["symbol"].isin(COMBO_SYMBOLS), ["date", "symbol", "close"]].dropna()
    missing_combo = sorted(set(COMBO_SYMBOLS) - set(market_history["symbol"]))
    if missing_combo:
        raise ValueError(f"research panel is missing combo symbols: {', '.join(missing_combo)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    panel_path = output_dir / "research_panel.csv.gz"
    market_path = output_dir / "market_history.csv.gz"
    manifest_path = output_dir / "manifest.json"
    lifecycle_panel.to_csv(panel_path, index=False, compression="gzip")
    market_history.to_csv(market_path, index=False, compression="gzip")
    manifest = {
        "contract_version": "crypto.lifecycle_preflight.v1",
        "panel_rows": int(len(lifecycle_panel)),
        "panel_symbols": sorted(lifecycle_panel["symbol"].unique().tolist()),
        "market_rows": int(len(market_history)),
        "market_symbols": sorted(market_history["symbol"].unique().tolist()),
        "start_date": lifecycle_panel["date"].min().date().isoformat(),
        "end_date": lifecycle_panel["date"].max().date().isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export real production research inputs for lifecycle drift preflight.")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--universe-mode", default="broad_liquid")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    result = run_research_pipeline(config, universe_mode=args.universe_mode)
    manifest = export_lifecycle_inputs(result["panel"], args.output_dir)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
