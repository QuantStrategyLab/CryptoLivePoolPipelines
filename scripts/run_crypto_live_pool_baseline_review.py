#!/usr/bin/env python3
"""Produce a fail-closed S2 baseline review for crypto live-pool rotation."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROFILE = "crypto_live_pool_rotation"
GATE_NAMES = {
    "H1": "falsifiable_hypothesis",
    "H2": "data_provenance",
    "H3": "sample_adequacy",
    "H4": "benchmark_comparison",
    "H5": "cost_model",
    "H6": "cost_stress",
    "H7": "oos_folds",
    "H8": "purge_embargo",
    "H9": "leakage_control",
    "H10": "parameter_stability",
    "H11": "risk_metrics",
    "H12": "reproducibility",
}


def _gate(gate_id: str, reason: str, *, status: str = "insufficient_evidence", refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": gate_id,
        "name": GATE_NAMES[gate_id],
        "status": status,
        "reason_codes": [reason],
        "evidence_refs": refs or [],
    }


def build_review(*, performance_summary: Path, walkforward_summary: Path) -> dict[str, Any]:
    missing = [str(path) for path in (performance_summary, walkforward_summary) if not _usable_csv(path)]
    reason = "MISSING_OR_INVALID_REAL_PERFORMANCE_ARTIFACT" if missing else "BASELINE_REVIEW_NOT_YET_FROZEN"
    gates = [_gate(gate_id, reason) for gate_id in GATE_NAMES]
    return {
        "schema_version": "strategy_review.v1",
        "profile": PROFILE,
        "decision": "insufficient_evidence",
        "promotion_allowed": False,
        "score": 0,
        "hard_gates": gates,
        "scorecard": {"total": 0, "max": 100, "scored_gates": 0},
        "blocking_reason_codes": [reason],
        "evidence": {
            "metrics_kind": "performance",
            "data_source": "unavailable" if missing else "unfrozen",
            "sample_count": 0,
            "oos_folds": 0,
            "placeholder_metrics": False,
            "provenance": {
                "snapshot": {"source_revision": "unavailable", "cost_model": "unavailable", "data_timestamp": "unavailable", "status": "unavailable"},
                "backtest": {"source_revision": "unavailable", "cost_model": "unavailable", "data_timestamp": "unavailable", "status": "unavailable"},
            },
            "missing_artifacts": missing,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "decision_packet": {
            "strategy_what": "按评分选择加密资产并进行 live-pool rotation；当前仅评审流程，不执行交易。",
            "return_source": "真实 performance artifacts 未提供，收益来源无法确认。",
            "loss_scenarios": "未完成真实回测，主要亏损场景无法确认；不得用 placeholder 指标替代。",
            "max_risk": "证据不足，最大回撤、容量和流动性风险均不可确认。",
            "evidence_sufficiency": "insufficient_evidence",
            "version_change": "新增 fail-closed 基线评审与人工决策 packet；未改变 live 参数。",
            "system_recommendation": "insufficient_evidence",
            "technical_evidence_refs": missing,
            "automation_boundary": {
                "research_auto_after_hard_gates": True,
                "shadow_auto_after_hard_gates": True,
                "canary_mode": "bounded_preapproved_only",
                "canary_limits": {"max_capital": 1000.0, "capital_currency": "USD", "max_duration_days": 14, "max_drawdown_fraction": 0.05, "max_leverage": 1.0, "max_concurrency": 1},
                "auto_scale_allowed": False,
                "normal_live_requires_human": True,
                "funding_leverage_risk_override_requires_human": True,
                "hard_risk_auto_pause_rollback": True,
            },
            "allowed_human_decisions": ["approve_research", "reject_rollback"],
        },
    }


def _usable_csv(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            data_row = next(reader, None)
        if not header or not data_row:
            return False
        normalized = {"".join(ch for ch in cell.lower() if ch.isalnum() or ch == "_") for cell in header}
        metric_columns = {"cagr", "sharpe", "calmar", "maxdrawdown", "max_dd", "annualizedvolatility"}
        if not normalized.intersection(metric_columns):
            return False
        numeric_values = []
        for value in data_row:
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                continue
        return bool(numeric_values)
    except (OSError, UnicodeError, csv.Error):
        return False


def render_markdown(review: dict[str, Any]) -> str:
    lines = [
        f"# Baseline review: `{review['profile']}`",
        "",
        f"- Decision: **{review['decision']}**",
        f"- Promotion allowed: **{review['promotion_allowed']}**",
        f"- Score: **{review['score']}/100**",
        "",
        "This report is fail-closed. No real performance artifact is promoted by this command.",
        "",
        "| Gate | Status | Reason |",
        "|---|---|---|",
    ]
    for gate in review["hard_gates"]:
        lines.append(f"| {gate['id']} {gate['name']} | {gate['status']} | {', '.join(gate['reason_codes'])} |")
    lines.extend(["", "## Blocking reasons", "", *[f"- `{reason}`" for reason in review["blocking_reason_codes"]]])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--performance-summary", type=Path, default=Path("data/reports/performance_summary.csv"))
    parser.add_argument("--walkforward-summary", type=Path, default=Path("data/reports/walkforward_validation_summary.csv"))
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(argv)
    review = build_review(performance_summary=args.performance_summary, walkforward_summary=args.walkforward_summary)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(review), encoding="utf-8")
    print(json.dumps({"decision": review["decision"], "promotion_allowed": False, "output": str(args.output_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
