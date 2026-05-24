from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
SCHEMA_VERSION = "2026-04-02"
REPO_ORDER = ["CryptoSnapshotPipelines"]
MANUAL_REVIEW_PREFIXES = (
    "check ",
    "review ",
    "audit ",
    "reconcile ",
    "assess ",
    "confirm ",
    "determine ",
    "verify ",
)


def _combined_action_text(action: dict[str, Any]) -> str:
    return f"{action.get('title', '')} {action.get('summary', '')}".lower()


def _resolve_owner_repo(source_review: dict[str, Any], action: dict[str, Any]) -> str:
    text = _combined_action_text(action)

    if any(
        marker in text
        for marker in (
            "shadow build",
            "challenger",
            "tie-break",
            "tie break",
            "equal scores",
            "boundary",
            "near-cutoff",
            "near-cutoff",
            "selection threshold",
        )
    ):
        return "CryptoSnapshotPipelines"

    return action["owner_repo"]


def _resolve_auto_pr_safe(source_review: dict[str, Any], action: dict[str, Any]) -> bool:
    title = str(action.get("title", "")).strip().lower()
    summary = str(action.get("summary", "")).strip().lower()
    if not bool(action.get("auto_pr_safe")):
        return False

    if title.startswith(MANUAL_REVIEW_PREFIXES):
        return False
    if any(
        phrase in summary
        for phrase in (
            "assess whether",
            "confirm whether",
            "determine whether",
            "verify minimum order size",
            "review logs",
            "pull binance transaction history",
        )
    ):
        return False
    return True


def highest_risk(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "low"
    return max(actions, key=lambda item: RISK_ORDER.get(str(item.get("risk_level", "low")), 0)).get("risk_level", "low")


def sort_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        actions,
        key=lambda item: (
            RISK_ORDER.get(str(item.get("risk_level", "low")), 0),
            str(item.get("title", "")),
        ),
        reverse=True,
    )


def normalize_action(source_review: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    owner_repo = _resolve_owner_repo(source_review, action)
    auto_pr_safe = _resolve_auto_pr_safe(source_review, action)
    return {
        "source_repo": source_review["source_repo"],
        "source_issue_number": source_review["source_issue"]["number"],
        "source_issue_title": source_review["source_issue"]["title"],
        "source_issue_url": source_review["source_issue"]["url"],
        "source_review_kind": source_review["review_kind"],
        "owner_repo": owner_repo,
        "title": action["title"],
        "risk_level": action["risk_level"],
        "auto_pr_safe": auto_pr_safe,
        "experiment_only": bool(action.get("experiment_only")),
        "summary": action["summary"],
    }


def build_plan(*source_reviews: dict[str, Any]) -> dict[str, Any]:
    if not source_reviews:
        raise ValueError("at least one source review is required")
    source_review_list = list(source_reviews)

    normalized_actions = [
        normalize_action(review, action)
        for review in source_review_list
        for action in review.get("recommended_actions", [])
    ]
    in_scope_actions = [
        action for action in normalized_actions if action["owner_repo"] in REPO_ORDER
    ]
    out_of_scope_actions = [
        action for action in normalized_actions if action["owner_repo"] not in REPO_ORDER
    ]
    repo_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for action in in_scope_actions:
        repo_groups[action["owner_repo"]].append(action)

    repo_action_summary = {
        repo: {
            "count": len(sort_actions(repo_groups.get(repo, []))),
            "highest_risk_level": highest_risk(repo_groups.get(repo, [])),
            "actions": sort_actions(repo_groups.get(repo, [])),
        }
        for repo in REPO_ORDER
        if repo_groups.get(repo)
    }

    safe_auto_pr_candidates = [action for action in in_scope_actions if action["auto_pr_safe"] and action["risk_level"] == "low"]
    experiment_candidates = [action for action in in_scope_actions if action["experiment_only"]]
    human_review_required = [
        action for action in in_scope_actions if (not action["auto_pr_safe"]) or action["risk_level"] != "low"
    ]
    operator_focus = [
        f"{review['source_repo']}: {review['summary']}"
        for review in source_review_list
    ]

    highest_review_risk = highest_risk([
        {"risk_level": review["risk_level"]}
        for review in source_review_list
    ])

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_reviews": source_review_list,
        "highest_review_risk": highest_review_risk,
        "repo_action_summary": repo_action_summary,
        "safe_auto_pr_candidates": sort_actions(safe_auto_pr_candidates),
        "experiment_candidates": sort_actions(experiment_candidates),
        "human_review_required": sort_actions(human_review_required),
        "out_of_scope_actions": sort_actions(out_of_scope_actions),
        "operator_focus": operator_focus,
    }


def render_summary_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Monthly Optimization Planner",
        "",
        f"- Highest review risk: `{plan['highest_review_risk']}`",
        f"- Safe auto-PR candidates: `{len(plan['safe_auto_pr_candidates'])}`",
        f"- Experiment candidates: `{len(plan['experiment_candidates'])}`",
        f"- Human review required: `{len(plan['human_review_required'])}`",
        f"- Out-of-scope downstream actions: `{len(plan.get('out_of_scope_actions', []))}`",
        "",
        "## Source Reviews",
    ]
    for review in plan["source_reviews"]:
        lines.extend(
            [
                f"- **{review['source_repo']}** `{review['risk_level']}` / `{review['production_recommendation']}`: {review['summary']}",
                f"  - Source issue: [{review['source_issue']['title']}]({review['source_issue']['url']})",
                f"  - Run: {review['run_url']}",
            ]
        )

    lines.extend(["", "## Recommended Work by Repo"])
    for repo in REPO_ORDER:
        repo_summary = plan["repo_action_summary"].get(repo)
        if not repo_summary:
            continue
        lines.extend(["", f"### {repo}"])
        for action in repo_summary["actions"]:
            flags: list[str] = []
            if action["auto_pr_safe"]:
                flags.append("auto-pr-safe")
            if action["experiment_only"]:
                flags.append("experiment-only")
            flag_suffix = f" [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"- `{action['risk_level']}` {action['title']}{flag_suffix}: {action['summary']} "
                f"(from {action['source_repo']} #{action['source_issue_number']})"
            )

    if plan["operator_focus"]:
        lines.extend(["", "## Operator Focus"])
        lines.extend(f"- {item}" for item in plan["operator_focus"])

    if plan.get("out_of_scope_actions"):
        lines.extend(["", "## Out-of-Scope Actions"])
        for action in plan["out_of_scope_actions"]:
            lines.append(
                f"- `{action['risk_level']}` {action['owner_repo']}: {action['title']} "
                f"(from {action['source_repo']} #{action['source_issue_number']})"
            )

    return "\n".join(lines).strip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the monthly optimization plan from upstream selector AI review payloads.",
    )
    parser.add_argument("--upstream-review-file", required=True, type=Path)
    parser.add_argument("--downstream-review-file", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_reviews = [json.loads(args.upstream_review_file.read_text(encoding="utf-8"))]
    if args.downstream_review_file:
        source_reviews.append(json.loads(args.downstream_review_file.read_text(encoding="utf-8")))
    plan = build_plan(*source_reviews)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "optimization_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "optimization_summary.md").write_text(
        render_summary_markdown(plan),
        encoding="utf-8",
    )
    print(f"optimization_plan={args.output_dir / 'optimization_plan.json'}")
    print(f"optimization_summary={args.output_dir / 'optimization_summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
