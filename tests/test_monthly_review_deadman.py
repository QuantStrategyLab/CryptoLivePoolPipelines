from datetime import datetime, timezone
import os
import unittest
from unittest.mock import Mock, patch

from scripts import check_monthly_review_deadman as monitor
from scripts.advisory_review_loop import deadman


def run(month, *, event="schedule", status="completed", branch="main", run_id=None):
    return {"id": run_id or month, "created_at": f"2026-{month:02}-01T03:15:00Z",
            "head_branch": branch, "status": status, "event": event}


def jobs(outcome):
    return [{"name": "monthly-dual-api-review", "conclusion": outcome}]


class ScheduledCycleTests(unittest.TestCase):
    now = datetime(2027, 1, 1, 6, tzinfo=timezone.utc)

    def test_mixed_failure_and_missing_cycles_reach_threshold(self):
        records = monitor.scheduled_cycles([run(11)], Mock(return_value=jobs("skipped")), self.now)
        self.assertEqual([r["outcome"] for r in records], ["missing", "missing", "skipped"])
        self.assertTrue(deadman([r["outcome"] for r in records], 3))

    def test_successful_manual_run_cannot_mask_a_missed_schedule(self):
        read = Mock(return_value=jobs("success"))
        records = monitor.scheduled_cycles([run(11, event="workflow_dispatch")], read, self.now)
        self.assertEqual([r["outcome"] for r in records], ["missing"] * 3)
        read.assert_not_called()

    def test_stalled_scheduled_review_is_unhealthy(self):
        read = Mock()
        for status in ("queued", "in_progress", "waiting", "pending"):
            with self.subTest(status=status):
                records = monitor.scheduled_cycles([run(11, status=status)], read, self.now)
                self.assertEqual(records[-1]["outcome"], "timed_out")
        read.assert_not_called()

    def test_grace_and_adoption_do_not_create_early_missed_cycles(self):
        for now in (datetime(2026, 10, 20, tzinfo=timezone.utc),
                    datetime(2026, 11, 1, 5, 14, tzinfo=timezone.utc)):
            self.assertEqual(monitor.scheduled_cycles([], Mock(), now), [])
        records = monitor.scheduled_cycles([], Mock(), datetime(2026, 11, 1, 5, 15, tzinfo=timezone.utc))
        self.assertEqual(records, [{"cycle": "2026-11", "outcome": "missing"}])

    def test_completed_publish_without_review_is_unhealthy(self):
        records = monitor.scheduled_cycles([run(11)], Mock(return_value=[]), self.now)
        self.assertEqual(records[-1]["outcome"], "missing")

    def test_recovery_breaks_the_unhealthy_streak(self):
        records = monitor.scheduled_cycles([run(12)], Mock(return_value=jobs("success")), self.now)
        self.assertFalse(deadman([r["outcome"] for r in records], 3))
        self.assertEqual(records[0]["outcome"], "missing")
        self.assertEqual(monitor.outage_start(records), "2027-01")

    def test_duplicate_or_null_job_result_never_passes(self):
        for result in (jobs("success") * 2, jobs(None)):
            with self.subTest(result=result):
                self.assertNotEqual(monitor.review_job_outcome(run(11), Mock(return_value=result)), "success")

    def test_other_branches_and_events_do_not_satisfy_schedule(self):
        records = monitor.scheduled_cycles([run(11, branch="feature"), run(12, event="pull_request")], Mock(), self.now)
        self.assertEqual([r["outcome"] for r in records], ["missing"] * 3)

    def test_manual_stall_has_grace_and_does_not_reset_scheduled_streak(self):
        pending = run(11, event="workflow_dispatch", status="in_progress")
        self.assertEqual(monitor.manual_cycles([pending], Mock(), datetime(2026, 11, 1, 4, tzinfo=timezone.utc)), [])
        records = monitor.manual_cycles([pending], Mock(), self.now)
        self.assertEqual(records, [{"cycle": "manual:11", "outcome": "timed_out"}])

    def test_dedup_identity_survives_new_failures_until_recovery(self):
        records = [{"cycle": "2026-12", "outcome": "missing"},
                   {"cycle": "2026-11", "outcome": "skipped"}]
        self.assertEqual(monitor.outage_start(records), "2026-11")
        self.assertEqual(monitor.outage_start([{"cycle": "2027-01", "outcome": "missing"}] + records), "2026-11")
        self.assertEqual(monitor.outage_start([{"cycle": "2027-01", "outcome": "failure"},
                                              {"cycle": "2026-12", "outcome": "success"}] + records), "2027-01")


class HistoryAndNotificationTests(unittest.TestCase):
    def test_partial_history_fails_visibly(self):
        with patch.object(monitor, "request_json", return_value={"total_count": 2, "workflow_runs": [run(11)]}):
            with self.assertRaises(ValueError):
                monitor.fetch_runs("https://api.github.com/repos/example/repo", {})

    def test_complete_history_is_paginated(self):
        first = [run(11, run_id=i) for i in range(1, 101)]
        with patch.object(monitor, "request_json", side_effect=[{"total_count": 101, "workflow_runs": first},
                                                               {"total_count": 101, "workflow_runs": [run(12, run_id=101)]}]) as api:
            self.assertEqual(len(monitor.fetch_runs("https://api.github.com/repos/example/repo", {})), 101)
            self.assertEqual(api.call_count, 2)

    def execute(self, api):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": monitor.REPOSITORY, "GITHUB_TOKEN": "synthetic-only"}), \
             patch.object(monitor, "fetch_runs", return_value=[]), \
             patch.object(monitor, "datetime") as clock, \
             patch.object(monitor, "request_json", side_effect=api):
            clock.now.return_value = datetime(2027, 1, 1, 6, tzinfo=timezone.utc)
            # scheduled_cycles needs the actual datetime constructor as well.
            clock.side_effect = datetime
            return monitor.main()

    def test_low_risk_path_only_reads_issues_and_creates_one_alert(self):
        requests = []
        def api(url, headers, *, payload=None):
            requests.append((url, payload))
            return [] if payload is None else {"number": 7}
        self.assertEqual(self.execute(api), 1)
        writes = [(url, payload) for url, payload in requests if payload is not None]
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0][0].endswith("/issues"))
        self.assertIn("@Pigbibi", writes[0][1]["body"])
        self.assertIn("operational alert", writes[0][1]["body"])
        self.assertNotIn("synthetic-only", str(writes))

    def test_ambiguous_issue_write_is_not_retried(self):
        writes = []
        def api(url, headers, *, payload=None):
            if payload is None:
                return []
            writes.append(url)
            raise TimeoutError("synthetic unknown write")
        self.assertEqual(self.execute(api), 2)
        self.assertEqual(len(writes), 1)

    def test_existing_bot_alert_is_deduplicated(self):
        first_payload = []
        def first(url, headers, *, payload=None):
            if payload is not None:
                first_payload.append(payload)
                return {"number": 7}
            return []
        self.execute(first)
        alert = {"body": first_payload[0]["body"], "user": {"login": "github-actions[bot]"}}
        api = Mock(return_value=[alert])
        self.assertEqual(self.execute(api), 1)
        self.assertEqual(api.call_count, 1)
        self.assertNotIn("payload", api.call_args.kwargs)
