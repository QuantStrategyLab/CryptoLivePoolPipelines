#!/usr/bin/env python3
"""Generate strategy_metrics.json consumed by AIAuditBridge strategy optimization watcher.

Reads the monthly shadow build summary and per-track release indexes, then writes a
JSON payload that the watcher can evaluate against degradation thresholds.

The watcher (AIAuditBridge service/strategy_optimization_policy.py) checks:
  sharpe, cagr, calmar, win_rate (higher-is-better, relative-drop threshold)
  max_dd (lower-is-better, absolute-worsening threshold)

The payload uses the versioned performance contract expected by the watcher. Missing
required metrics remain visible as data-quality findings instead of being treated as
a healthy strategy.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_REPO = "QuantStrategyLab/CryptoLivePoolPipelines"
PERFORMANCE_SCHEMA_VERSION = "strategy_performance.v2"
METRICS_KIND_PERFORMANCE = "performance"


def _canonical_metric_name(name: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")
    return {
        "max_drawdown": "max_dd",
        "winrate": "win_rate",
        "annualized_volatility": "volatility",
    }.get(normalized, normalized)


def _safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _track_metrics(index_table: pd.DataFrame) -> dict[str, Any]:
    """Extract canonical current and baseline performance metrics."""
    if index_table.empty:
        return {"current_metrics": {}, "baseline_metrics": {}}
    latest = index_table.iloc[-1]
    numeric_columns = index_table.select_dtypes(include=["number"]).columns.tolist()

    current: dict[str, float] = {}
    baseline: dict[str, float] = {}
    for col in numeric_columns:
        metric_name = _canonical_metric_name(col)
        cur = _safe_float(latest.get(col))
        values = index_table[col].map(_safe_float).dropna()
        base = _safe_float(values.abs().mean() if metric_name == "max_dd" else values.mean())
        if metric_name == "max_dd" and cur is not None:
            cur = abs(cur)
        if cur is not None:
            current[metric_name] = cur
        if base is not None:
            baseline[metric_name] = base

    return {"current_metrics": current, "baseline_metrics": baseline}


def _snapshot_payload(
    *,
    repo: str,
    profile: str,
    plugin: str,
    metrics: dict[str, Any],
    source: str,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "repo": repo,
        "strategy_profile": profile,
        "plugin": plugin,
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "metrics_kind": METRICS_KIND_PERFORMANCE,
        "current_metrics": metrics["current_metrics"],
        "baseline_metrics": metrics["baseline_metrics"],
        "source": source,
        "generated_at": generated_at,
    }


def generate_strategy_metrics(
    summary_path: Path,
    *,
    repo: str = DEFAULT_REPO,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Generate the strategy_metrics.json payload."""
    if not summary_path.exists():
        raise FileNotFoundError(f"shadow build summary not found: {summary_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    snapshots: list[dict[str, Any]] = []

    # ── official baseline track ──────────────────────────────────────
    baseline_cfg = summary.get("official_baseline", {})
    baseline_profile = str(baseline_cfg.get("profile") or "baseline_blended_rank")

    baseline_live_pool = baseline_cfg.get("live_pool_path")
    if baseline_live_pool:
        baseline_dir = Path(baseline_live_pool).parent.parent  # .../version/live_pool.json → parent dir
        baseline_index = baseline_dir / "release_index.csv"
        if baseline_index.exists():
            index = pd.read_csv(baseline_index)
            metrics = _track_metrics(index)
            snapshots.append(_snapshot_payload(
                repo=repo,
                profile=baseline_profile,
                plugin="",
                metrics=metrics,
                source=str(summary_path),
                generated_at=generated_at,
            ))

    # ── shadow candidate tracks ─────────────────────────────────────
    shadow_cfg = summary.get("shadow_candidate_tracks", {})
    tracks = shadow_cfg.get("tracks", [])
    if not isinstance(tracks, list):
        tracks = []

    _root_dir = Path(shadow_cfg.get("root_dir", ""))  # noqa: F841
    for track in tracks:
        if not isinstance(track, dict):
            continue
        track_id = str(track.get("track_id") or "")
        profile_name = str(track.get("profile_name") or "")
        release_index_path = track.get("release_index_path")

        index_path: Path | None = None
        if release_index_path:
            index_path = Path(release_index_path)
            if not index_path.is_absolute():
                index_path = PROJECT_ROOT / index_path

        if index_path is None or not index_path.exists():
            snapshots.append(_snapshot_payload(
                repo=repo,
                profile=profile_name,
                plugin=track_id,
                metrics={"current_metrics": {}, "baseline_metrics": {}},
                source=str(index_path) if index_path else "",
                generated_at=generated_at,
            ))
            continue

        index = pd.read_csv(index_path)
        metrics = _track_metrics(index)
        snapshots.append(_snapshot_payload(
            repo=repo,
            profile=profile_name,
            plugin=track_id,
            metrics=metrics,
            source=str(index_path),
            generated_at=generated_at,
        ))

    payload: dict[str, Any] = {
        "repo": repo,
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "metrics_kind": METRICS_KIND_PERFORMANCE,
        "generated_at": generated_at,
        "source": "monthly_shadow_build",
        "snapshots": snapshots,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"Wrote strategy_metrics.json → {output_path}")
        print(f"  snapshots: {len(snapshots)}")
        for s in snapshots:
            profile = s.get("strategy_profile", "")
            n_metrics = len(s.get("current_metrics", {}))
            print(f"  - {profile}: {n_metrics} metrics")

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate strategy_metrics.json for the AIAuditBridge strategy optimization watcher."
    )
    parser.add_argument(
        "--summary",
        default="data/output/monthly_shadow_build_summary.json",
        help="Path to the shadow build summary JSON (default: data/output/monthly_shadow_build_summary.json)",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("STRATEGY_METRICS_REPO", DEFAULT_REPO),
        help="Repository identifier for the metrics payload",
    )
    parser.add_argument(
        "--output",
        default="data/output/strategy_metrics.json",
        help="Output path for strategy_metrics.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary_path = Path(args.summary)
    if not summary_path.is_absolute():
        summary_path = PROJECT_ROOT / summary_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    try:
        generate_strategy_metrics(
            summary_path,
            repo=args.repo,
            output_path=output_path,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Error: invalid input format — {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
