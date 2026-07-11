"""Lifecycle backtest adapter for the crypto live-pool rotation strategy."""

from __future__ import annotations

from datetime import date
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

from .orchestrator_runner import CryptoLivePoolBacktestRunner, PROFILE_NAME


class InsufficientEvidenceError(RuntimeError):
    """Raised when lifecycle wiring does not provide a real market panel."""


PREFLIGHT_V1 = "crypto.lifecycle_preflight.v1"
PREFLIGHT_V2 = "crypto.lifecycle_preflight.v2"
PREFLIGHT_ENV = "CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"


def load_preflight_panel(expected_end_date: date | None = None) -> pd.DataFrame:
    configured = os.environ.get(PREFLIGHT_ENV) or os.environ.get("PREFLIGHT_BUNDLE_ROOT")
    if not configured:
        raise InsufficientEvidenceError(f"{PREFLIGHT_ENV} is required for no-arg lifecycle registration")
    root = Path(configured).expanduser().resolve()
    if not (root / "research_panel.csv.gz").exists():
        raise InsufficientEvidenceError(f"preflight bundle missing research_panel.csv.gz: {root}")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        panel = pd.read_csv(root / "research_panel.csv.gz", compression="gzip")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise InsufficientEvidenceError(f"invalid lifecycle preflight bundle: {root}") from exc
    contract_version = manifest.get("contract_version")
    if contract_version not in {PREFLIGHT_V1, PREFLIGHT_V2}:
        raise InsufficientEvidenceError("lifecycle preflight manifest mismatch")
    if contract_version == PREFLIGHT_V2:
        required_manifest = {
            "contract_version", "domain", "producer", "strategy_profile", "panel_rows", "panel_symbols",
            "market_rows", "market_symbols", "start_date", "end_date", "market_start_date", "market_end_date",
        }
        if not required_manifest.issubset(manifest):
            raise InsufficientEvidenceError("v2 lifecycle preflight manifest fields are incomplete")
        if manifest["domain"] != "crypto" or manifest["producer"] != "export_lifecycle_preflight_inputs.py" or manifest["strategy_profile"] != PROFILE_NAME:
            raise InsufficientEvidenceError("v2 lifecycle preflight identity mismatch")
    else:
        if manifest.get("producer") not in {None, "export_lifecycle_preflight_inputs.py"}:
            raise InsufficientEvidenceError("v1 lifecycle preflight producer mismatch")
        if manifest.get("strategy_profile") not in {None, PROFILE_NAME}:
            raise InsufficientEvidenceError("v1 lifecycle preflight strategy_profile mismatch")
    required = {"date", "symbol", "in_universe", "open", "final_score"}
    if not required.issubset(panel.columns):
        raise InsufficientEvidenceError("research_panel.csv.gz missing required columns")
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    panel["open"] = pd.to_numeric(panel["open"], errors="coerce")
    panel["final_score"] = pd.to_numeric(panel["final_score"], errors="coerce")
    universe_values = panel["in_universe"].map(
        lambda value: value if isinstance(value, bool) else {
            "true": True, "1": True, "false": False, "0": False,
        }.get(str(value).strip().lower())
    )
    if universe_values.isna().any():
        raise InsufficientEvidenceError("research_panel.csv.gz contains invalid in_universe values")
    panel["in_universe"] = universe_values.astype(bool)
    if panel.empty or panel["date"].isna().any() or panel["open"].isna().any() or not panel["open"].map(math.isfinite).all():
        raise InsufficientEvidenceError("research_panel.csv.gz contains invalid numeric/date content")
    scored_dates = panel.loc[panel["final_score"].notna(), "date"]
    if not panel["final_score"].dropna().map(math.isfinite).all():
        raise InsufficientEvidenceError("research_panel.csv.gz contains non-finite scores")
    market_path = root / "market_history.csv.gz"
    if not market_path.exists():
        raise InsufficientEvidenceError("preflight bundle missing market_history.csv.gz")
    try:
        market = pd.read_csv(market_path, usecols=["date", "symbol", "close"], compression="gzip")
    except (OSError, UnicodeError, ValueError) as exc:
        raise InsufficientEvidenceError("invalid market_history.csv.gz") from exc
    if not {"date", "symbol", "close"}.issubset(market.columns):
        raise InsufficientEvidenceError("market_history.csv.gz missing required columns")
    market["date"] = pd.to_datetime(market["date"], errors="coerce")
    market["close"] = pd.to_numeric(market["close"], errors="coerce")
    if market.empty or market["date"].isna().any() or market["close"].isna().any() or not market["close"].map(math.isfinite).all():
        raise InsufficientEvidenceError("market_history.csv.gz contains invalid content")
    if manifest.get("panel_rows") is not None and (manifest["panel_rows"] != len(panel) or sorted(manifest.get("panel_symbols", [])) != sorted(panel["symbol"].dropna().unique().tolist())):
        raise InsufficientEvidenceError("research panel does not match manifest counts or symbols")
    if manifest.get("market_rows") is not None and (manifest["market_rows"] != len(market) or sorted(manifest.get("market_symbols", [])) != sorted(market["symbol"].dropna().unique().tolist())):
        raise InsufficientEvidenceError("market history does not match manifest counts or symbols")
    if panel["final_score"].notna().sum() == 0:
        raise InsufficientEvidenceError("research_panel.csv.gz has no valid scored rows")
    in_universe = panel["in_universe"]
    if panel.loc[in_universe, "final_score"].isna().any():
        raise InsufficientEvidenceError("research_panel.csv.gz has malformed scores for in-universe rows")
    panel_end_date = scored_dates.dt.normalize().max().date() if not scored_dates.empty else panel["date"].dt.normalize().max().date()
    if expected_end_date is not None and panel_end_date < expected_end_date:
        raise InsufficientEvidenceError("research panel ends before requested evaluation window")
    freshness_reference = date.today()
    if (freshness_reference - panel_end_date).days > 3:
        raise InsufficientEvidenceError("research panel preflight artifact is stale")
    if contract_version == PREFLIGHT_V2:
        if manifest["start_date"] != scored_dates.dt.normalize().min().date().isoformat() or manifest["end_date"] != panel_end_date.isoformat():
            raise InsufficientEvidenceError("v2 panel date range does not match manifest")
        market_start = market["date"].dt.normalize().min().date().isoformat()
        market_end = market["date"].dt.normalize().max().date().isoformat()
        if manifest["market_start_date"] != market_start or manifest["market_end_date"] != market_end:
            raise InsufficientEvidenceError("v2 market date range does not match manifest")
    return panel.set_index(["date", "symbol"]).sort_index()


class CryptoBacktestRunner:
    """Expose the real crypto backtest engine through the lifecycle contract.

    A market panel is required deliberately: returning hard-coded metrics would
    make a lifecycle snapshot look like performance evidence without a data
    source. Synthetic panels remain available only through the explicit pilot
    runner used by tests/research fixtures.
    """

    def __init__(self, *, panel: pd.DataFrame | None = None) -> None:
        self._panel = panel
        self._runner: CryptoLivePoolBacktestRunner | None = None

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        if self._panel is None:
            self._runner = CryptoLivePoolBacktestRunner(panel=load_preflight_panel(end_date))
        elif self._runner is None:
            self._runner = CryptoLivePoolBacktestRunner(panel=self._panel)
        return self._runner.run(strategy_profile, params, start_date=start_date, end_date=end_date)


def build_backtest_runner(*, panel: pd.DataFrame | None = None) -> CryptoBacktestRunner:
    """Build the real adapter; lifecycle wiring must inject a prepared panel."""
    return CryptoBacktestRunner(panel=panel)
