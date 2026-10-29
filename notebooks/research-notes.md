# Research Notes

This repository is intentionally code-first rather than notebook-first.

Suggested workflow:

1. Run `scripts/download_history.py` to build the local Binance Spot daily history cache.
2. Run `scripts/run_walkforward_validation.py` for strict out-of-sample evaluation.
3. Run `scripts/run_research_backtest.py` to generate comparison plots and reports.
4. Run `scripts/build_live_pool.py` to export the latest universe and leader pool files for the downstream strategy script.

When you inspect a specific historical date, use `scripts/debug_single_date_snapshot.py` rather than ad hoc notebook code so the snapshot remains reproducible.
