"""Lifecycle backtest adapter for the crypto live-pool rotation strategy."""

from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult

from .orchestrator_runner import CryptoLivePoolBacktestRunner, PROFILE_NAME


class InsufficientEvidenceError(RuntimeError):
    """Raised when lifecycle wiring does not provide a real market panel."""


PREFLIGHT_CONTRACT_VERSION = "crypto.lifecycle_preflight.v1"
PREFLIGHT_ENV = "CRYPTO_LIFECYCLE_PREFLIGHT_ROOT"


def load_preflight_panel() -> pd.DataFrame:
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
    if manifest.get("contract_version") != PREFLIGHT_CONTRACT_VERSION:
        raise InsufficientEvidenceError("lifecycle preflight manifest mismatch")
    if manifest.get("strategy_profile", PROFILE_NAME) != PROFILE_NAME:
        raise InsufficientEvidenceError("lifecycle preflight strategy_profile mismatch")
    required = {"date", "symbol", "in_universe", "open", "final_score"}
    if not required.issubset(panel.columns):
        raise InsufficientEvidenceError("research_panel.csv.gz missing required columns")
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
    panel["open"] = pd.to_numeric(panel["open"], errors="coerce")
    panel["final_score"] = pd.to_numeric(panel["final_score"], errors="coerce")
    if panel.empty or panel["date"].isna().any() or panel["open"].isna().any() or panel["final_score"].isna().any():
        raise InsufficientEvidenceError("research_panel.csv.gz contains invalid numeric/date content")
    if (date.today() - panel["date"].dt.normalize().max().date()).days > 3:
        raise InsufficientEvidenceError("research panel preflight artifact is stale")
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
        if self._runner is None:
            self._runner = CryptoLivePoolBacktestRunner(panel=self._panel if self._panel is not None else load_preflight_panel())
        return self._runner.run(strategy_profile, params, start_date=start_date, end_date=end_date)


def build_backtest_runner(*, panel: pd.DataFrame | None = None) -> CryptoBacktestRunner:
    """Build the real adapter; lifecycle wiring must inject a prepared panel."""
    return CryptoBacktestRunner(panel=panel)
