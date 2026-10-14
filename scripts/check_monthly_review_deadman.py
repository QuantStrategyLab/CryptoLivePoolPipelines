"""Independent review liveness check. Never dispatch publication or trading work."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.advisory_review_loop import deadman
from scripts.run_monthly_dual_api_review import REPOSITORY, request_json


def review_outcomes(runs, jobs, *, since="2026-10-15T00:00:00Z"):
    outcomes = []
    for run in sorted(runs, key=lambda item: item["created_at"], reverse=True):
        if (run.get("head_branch") != "main" or run.get("status") != "completed"
                or run["created_at"] < since or run.get("event") not in {"schedule", "workflow_dispatch"}):
            continue
        matches = [item for item in jobs(run["id"]) if item.get("name") == "monthly-dual-api-review"]
        conclusion = matches[0].get("conclusion") if len(matches) == 1 else "missing"
        outcomes.append("success" if conclusion == "success" else conclusion
                        if conclusion in {"skipped", "cancelled", "timed_out"} else "failure")
    return outcomes


def missed_months(runs, now):
    # First natural cycle after this feature was introduced. Allow two hours for completion.
    year, month, missed = 2026, 11, 0
    while datetime(year, month, 1, 5, 15, tzinfo=timezone.utc) < now:
        prefix = f"{year:04}-{month:02}"
        if not any(run.get("created_at", "").startswith(prefix) and run.get("head_branch") == "main"
                   and run.get("event") in {"schedule", "workflow_dispatch"} for run in runs):
            missed += 1
        else:
            missed = 0
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return missed


def main():
    try:
        if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
            raise ValueError("wrong repository")
        threshold = int(os.environ.get("REVIEW_DEADMAN_THRESHOLD", "3"))
        owner = os.environ.get("REVIEW_OWNER_LOGIN", "Pigbibi")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
            raise ValueError("invalid owner")
        headers = {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                   "Accept": "application/vnd.github+json"}
        base = "https://api.github.com/repos/" + REPOSITORY
        runs = request_json(base + "/actions/workflows/monthly_publish.yml/runs?branch=main&per_page=100", headers)["workflow_runs"]

        def jobs(run_id):
            result = request_json(base + f"/actions/runs/{run_id}/jobs?per_page=100", headers)
            if result["total_count"] > len(result["jobs"]):
                raise ValueError("incomplete review job history")
            return result["jobs"]

        outcomes = review_outcomes(runs, jobs)
        missing = missed_months(runs, datetime.now(timezone.utc))
        unhealthy = deadman(outcomes, threshold) or missing >= threshold
        if not unhealthy:
            print(json.dumps({"review_deadman": "healthy", "observed_cycles": len(outcomes)}))
            return 0
        fingerprint = hashlib.sha256(json.dumps({"runs": [r["id"] for r in runs[:threshold]],
                                                 "missing": missing}).encode()).hexdigest()
        marker = "<!-- monthly-review-deadman:" + fingerprint + " -->"
        # Bound and paginated lookup; never blindly repeat an ambiguous issue POST.
        for page in range(1, 101):
            issues = request_json(base + f"/issues?state=all&per_page=100&page={page}", headers)
            if any(marker in item.get("body", "") and item.get("user", {}).get("login") == "github-actions[bot]"
                   for item in issues):
                print("Review dead-man alert already recorded.")
                return 1
            if len(issues) < 100:
                break
        else:
            raise ValueError("incomplete dead-man alert history")
        request_json(base + "/issues", headers, payload={
            "title": "Monthly AI review dead-man alert",
            "body": marker + f"\n@{owner} Monthly review failed, skipped, or was missing for at least {threshold} cycles. "
                    "Check the review job and its existing grants/secrets. This is an operational alert, "
                    "not a confirmed financial finding. No publication, orders, configuration or permissions were changed.",
        })
        print("Review dead-man owner alert recorded.")
        return 1
    except Exception:
        print("Review dead-man check unavailable; inspect this failed monitor job. No retries or live actions.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
