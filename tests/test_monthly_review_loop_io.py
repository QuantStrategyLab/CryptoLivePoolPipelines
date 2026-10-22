import json
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

from scripts.check_monthly_review_deadman import missed_months, review_outcomes
from scripts.monthly_review_loop import IssueState, apply_monthly_loop


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

    def test_monthly_pass_only_records_shadow_without_closing_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = FakeIssue()
            path = Path(tmp) / "verdict.json"
            values = [(r, {"verdict": "approve", "severity": "none", "summary": "Evidence checked."})
                      for r in ("OpenAI", "Anthropic")]
            state = apply_monthly_loop(issue, values, identity="frozen", materials="evidence", archive=path, env={})
            self.assertFalse(state["finished"])
            self.assertEqual(json.loads(path.read_text())["disposition"], "shadow_recorded")
            self.assertFalse(state["ai_submitted"])
            issue.auto_close.assert_not_called()
            self.assertEqual(issue.comments, [])

    def test_no_existing_dot_route_records_gap_without_notifying(self):
        with tempfile.TemporaryDirectory() as tmp:
            issue = FakeIssue()
            values = [(r, {"verdict": "flag", "severity": "none", "summary": "Concern."})
                      for r in ("OpenAI", "Anthropic")]
            result = apply_monthly_loop(issue, values, identity="frozen", materials="evidence",
                                        archive=Path(tmp) / "v.json", env={})
            self.assertEqual(result["binding_status"], "blocked_configuration")
            self.assertNotIn("@Pigbibi", json.dumps(issue.comments))
            issue.auto_close.assert_not_called()

    def test_configured_shadow_never_touches_injected_remote_or_issue(self):
        env = {"AI_SERVICE_URL": "https://gateway.example/mcp", "AI_SERVICE_AUDIENCE": "approved",
               "AI_SERVICE_PROFILE": "primary", "AI_SERVICE_MODEL": "configured-dot",
               "AI_SERVICE_TARGET": "quant-ai-service-v2", "AI_HOOK_MODE": "shadow"}
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError("external capability accessed: " + name)
        with tempfile.TemporaryDirectory() as tmp:
            values = [(role, {"verdict": "flag", "severity": "risk_limit", "summary": "Concern."})
                      for role in ("OpenAI", "Anthropic")]
            result = apply_monthly_loop(Forbidden(), values, identity="frozen", materials="sensitive-marker",
                                        archive=Path(tmp)/"verdict.json", env=env, dot=Forbidden())
            self.assertEqual(result["binding_status"], "present_unverified")
            self.assertNotIn("sensitive-marker", (Path(tmp)/"verdict.json").read_text())

    def test_monitor_counts_skipped_job_even_when_publish_workflow_succeeded(self):
        run = {"id": 1, "created_at": "2026-11-01T04:00:00Z", "head_branch": "main",
               "status": "completed", "event": "schedule", "conclusion": "success"}
        jobs = Mock(return_value=[{"name": "monthly-dual-api-review", "conclusion": "skipped"}])
        self.assertEqual(review_outcomes([run], jobs), ["skipped"])
        self.assertEqual(missed_months([], datetime(2027, 2, 1, tzinfo=timezone.utc)), 3)
