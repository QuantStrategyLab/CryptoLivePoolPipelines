import json
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from scripts.check_monthly_review_deadman import missed_months, review_outcomes
from scripts.monthly_review_loop import DotGateway, IssueState, apply_monthly_loop


class FakeIssue:
    def __init__(self):
        self.comments = []
        self.auto_close = Mock()

    def own_comments(self):
        return self.comments

    def post(self, body):
        self.comments.append({"body": body})


class LoopIOTests(unittest.TestCase):
    def test_state_survives_new_process_and_comment_delimiter_in_model_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = FakeIssue()
            first = IssueState(issue, archive=Path(tmp) / "a.json")
            first.save({"identity": "frozen", "summary": "untrusted --> text"})
            second = IssueState(issue, archive=Path(tmp) / "b.json")
            self.assertEqual(second.load("frozen")["summary"], "untrusted --> text")

    def test_monthly_pass_closes_and_creates_valid_verdict_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = FakeIssue()
            path = Path(tmp) / "verdict.json"
            values = [(r, {"verdict": "approve", "severity": "none", "summary": "Evidence checked."})
                      for r in ("OpenAI", "Anthropic")]
            state = apply_monthly_loop(issue, values, identity="frozen", materials="evidence", archive=path, env={})
            self.assertTrue(state["finished"])
            self.assertEqual(json.loads(path.read_text())["disposition"], "auto_verified")
            issue.auto_close.assert_called_once()

    def test_no_existing_dot_route_records_gap_without_notifying(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = FakeIssue()
            values = [(r, {"verdict": "flag", "severity": "none", "summary": "Concern."})
                      for r in ("OpenAI", "Anthropic")]
            result = apply_monthly_loop(issue, values, identity="frozen", materials="evidence",
                                        archive=Path(tmp) / "v.json", env={})
            self.assertEqual(result["gap"], "approved_dot_route_unavailable")
            self.assertNotIn("@Pigbibi", json.dumps(issue.comments))
            issue.auto_close.assert_not_called()

    def test_dot_receipt_must_match_request_owner_and_provider(self):
        env = {"AI_SERVICE_URL": "https://gateway.example/mcp", "AI_SERVICE_AUDIENCE": "approved",
               "AI_SERVICE_PROFILE": "primary", "AI_SERVICE_MODEL": "configured-dot",
               "GITHUB_REPOSITORY": "QuantStrategyLab/CryptoLivePoolPipelines"}
        http = Mock(return_value={"id": "task", "request": {}, "provider": "other", "status": "completed"})
        dot = DotGateway(env, http)
        dot._token = Mock(return_value="fake")
        with self.assertRaises(ValueError):
            dot.result("task", {})
        self.assertEqual(http.call_count, 1)

    def test_monitor_counts_skipped_job_even_when_publish_workflow_succeeded(self):
        run = {"id": 1, "created_at": "2026-11-01T04:00:00Z", "head_branch": "main",
               "status": "completed", "event": "schedule", "conclusion": "success"}
        jobs = Mock(return_value=[{"name": "monthly-dual-api-review", "conclusion": "skipped"}])
        self.assertEqual(review_outcomes([run], jobs), ["skipped"])
        self.assertEqual(missed_months([], datetime(2027, 2, 1, tzinfo=timezone.utc)), 3)
