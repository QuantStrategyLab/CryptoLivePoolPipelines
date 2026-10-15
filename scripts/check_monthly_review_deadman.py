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
from scripts.run_monthly_dual_api_review import request_json

FIRST_SCHEDULED_CYCLE = (2026, 11)
INTRODUCED_AT = "2026-10-15T00:00:00Z"


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def review_job_outcome(run, jobs):
    if run.get("status") != "completed":
        return "timed_out"
    matches = [item for item in jobs(run["id"]) if item.get("name") == "monthly-dual-api-review"]
    conclusion = matches[0].get("conclusion") if len(matches) == 1 else "missing"
    return ("success" if conclusion == "success" else conclusion
            if conclusion in {"skipped", "cancelled", "timed_out", "missing"} else "failure")


def scheduled_cycles(runs, jobs, now):
    """Newest expected monthly slot first, including missing and stalled runs.

    Match natural schedules only: successful manual/probe runs cannot conceal a
    missing or failed natural cycle. The existing schedule is 03:15 UTC on day 1;
    allow two hours before observing that slot. No publication is dispatched.
    """
    if now.tzinfo is None:
        raise ValueError("timezone required")
    year, month = FIRST_SCHEDULED_CYCLE
    cycles = []
    while datetime(year, month, 1, 5, 15, tzinfo=timezone.utc) <= now:
        key = f"{year:04}-{month:02}"
        matches = [run for run in runs if run.get("head_branch") == "main"
                   and run.get("event") == "schedule"
                   and timestamp(run["created_at"]).strftime("%Y-%m") == key]
        latest = max(matches, key=lambda run: timestamp(run["created_at"])) if matches else None
        cycles.append({"cycle": key, "outcome": "missing" if latest is None
                       else review_job_outcome(latest, jobs)})
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return list(reversed(cycles))


def manual_cycles(runs, jobs, now):
    cycles = []
    for run in sorted(runs, key=lambda item: timestamp(item["created_at"]), reverse=True):
        if (run.get("head_branch") != "main" or run.get("event") != "workflow_dispatch"
                or timestamp(run["created_at"]) < timestamp(INTRODUCED_AT)):
            continue
        if run.get("status") != "completed" and (now - timestamp(run["created_at"])).total_seconds() < 7200:
            continue
        cycles.append({"cycle": "manual:" + str(run["id"]), "outcome": review_job_outcome(run, jobs)})
    return cycles


def outage_start(cycles):
    """Stable across additional failures, different after an observed recovery."""
    start = None
    for cycle in cycles:
        if cycle["outcome"] == "success":
            break
        start = cycle["cycle"]
    if start is None:
        raise ValueError("no unhealthy streak")
    return start


def fetch_runs(base, headers):
    """Read complete adoption history, or fail visibly rather than declare health."""
    runs = []
    expected = None
    for page in range(1, 101):
        result = request_json(base + "/actions/workflows/monthly_publish.yml/runs"
                              f"?branch=main&created=%3E%3D2026-10-15&per_page=100&page={page}", headers)
        count = result["total_count"]
        if type(count) is not int or count < 0 or (expected is not None and expected != count):
            raise ValueError("inconsistent review run history")
        expected = count
        batch = result["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100 or len(runs) == expected:
            if len(runs) != expected or len({run["id"] for run in runs}) != len(runs):
                raise ValueError("incomplete or duplicate review run history")
            return runs
    raise ValueError("incomplete review run history")


def review_outcomes(runs, jobs, *, since="2026-10-15T00:00:00Z"):
    outcomes = []
    for run in sorted(runs, key=lambda item: item["created_at"], reverse=True):
        if (run.get("head_branch") != "main" or run.get("status") != "completed"
                or run["created_at"] < since or run.get("event") not in {"schedule", "workflow_dispatch"}):
            continue
        outcomes.append(review_job_outcome(run, jobs))
    return outcomes


def missed_months(runs, now):
    # Compatibility helper; scheduling presence alone is not review success.
    missing = 0
    for cycle in scheduled_cycles(runs, lambda _: [], now):
        if cycle["outcome"] != "missing":
            break
        missing += 1
    return missing


def main():
    try:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid repository")
        threshold = int(os.environ.get("REVIEW_DEADMAN_THRESHOLD", "3"))
        if not 1 <= threshold <= 24:
            raise ValueError("invalid dead-man threshold")
        owner = os.environ.get("REVIEW_OWNER_LOGIN", repository.split("/", 1)[0])
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
            raise ValueError("invalid owner")
        headers = {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                   "Accept": "application/vnd.github+json"}
        base = "https://api.github.com/repos/" + repository
        runs = fetch_runs(base, headers)

        def jobs(run_id):
            result = request_json(base + f"/actions/runs/{run_id}/jobs?per_page=100", headers)
            if result["total_count"] != len(result["jobs"]):
                raise ValueError("incomplete review job history")
            return result["jobs"]

        now = datetime.now(timezone.utc)
        scheduled = scheduled_cycles(runs, jobs, now)
        manual = manual_cycles(runs, jobs, now)
        unhealthy = [(kind, cycles) for kind, cycles in (("scheduled", scheduled), ("manual", manual))
                     if deadman([cycle["outcome"] for cycle in cycles], threshold)]
        if not unhealthy:
            print(json.dumps({"review_deadman": "healthy", "observed_cycles": len(scheduled) + len(manual)}))
            return 0
        # Choose natural-cycle failure first; one stable record per outage.
        kind, cycles = unhealthy[0]
        fingerprint = hashlib.sha256((kind + ":" + outage_start(cycles)).encode()).hexdigest()
        marker = "<!-- monthly-review-deadman:" + fingerprint + " -->"
        # Bound and paginated lookup; never blindly repeat an ambiguous issue POST.
        for page in range(1, 101):
            issues = request_json(base + f"/issues?state=all&per_page=100&page={page}", headers)
            if any(marker in (item.get("body") or "") and "pull_request" not in item
                   and item.get("user", {}).get("login") == "github-actions[bot]"
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
                    f"Observed {kind} cycle outcomes: " + json.dumps(cycles[:threshold], sort_keys=True) + ". "
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
