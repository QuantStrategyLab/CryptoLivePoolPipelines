from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    "monthly_dual_api_review", Path(__file__).resolve().parents[1] / "scripts/run_monthly_dual_api_review.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


class FakeIssue:
    def __init__(self, history=()):
        self.comments = []
        self.ledger = list(history)
        self.created_at = "2026-10-15T01:00:00Z"

    def own_comments(self):
        return self.ledger

    def auto_close(self):
        self.closed = True

    def history(self, month):
        return self.ledger

    def post(self, body):
        self.comments.append(body)
        self.ledger.append({"body": body, "created_at": self.created_at,
                            "user": {"login": "github-actions[bot]"}})


def ledger(amount="0.95", report_month="2026-09", month="2026-10"):
    return {"body": MODULE.LEDGER_PREFIX + json.dumps({"reserved_usd": amount,
            "report_month": report_month, "budget_month": month}) + " -->",
            "created_at": f"{month}-01T00:00:00Z", "user": {"login": "github-actions[bot]"}}


class MonthlyDualAPIReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bundle = Path(self.temp.name)
        (self.bundle / "monthly_report_bundle.json").write_text(
            json.dumps({"report_month": "2026-10", "as_of_date": "2026-10-01"}))
        (self.bundle / "ai_review_input.md").write_text("Published October upstream report", encoding="utf-8")
        (self.bundle / "strategy_metrics.json").write_text('{"snapshots": []}')
        self.config = MODULE.Config({})
        self.issue = FakeIssue()
        self.clients = {name: Mock(review=Mock(return_value=json.dumps({"verdict": "approve", "severity": "none", "summary": "Review finding"})))
                        for name in ("OpenAI", "Anthropic")}

    def run_review(self):
        return MODULE.run_review(self.bundle, self.config, self.issue, self.clients, NOW)

    def test_exactly_two_single_turn_requests_with_caps_and_no_tools(self):
        responses = [
            {"choices": [{"message": {"content": json.dumps({"verdict": "approve", "severity": "none", "summary": "OpenAI finding"})}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            {"content": [{"type": "text", "text": json.dumps({"verdict": "approve", "severity": "none", "summary": "Anthropic finding"})}], "stop_reason": "end_turn",
             "usage": {"input_tokens": 100, "output_tokens": 50}},
        ]
        self.clients = {provider: MODULE.APIReviewer(provider, "fake-key") for provider in MODULE.MODELS}
        with patch.object(MODULE, "request_json", side_effect=responses) as request:
            self.assertTrue(self.run_review())
        self.assertEqual(request.call_count, 2)
        for call in request.call_args_list:
            payload = call.kwargs["payload"]
            self.assertNotIn("tools", payload)
            self.assertNotIn("stream", payload)
            users = [message for message in payload["messages"] if message["role"] == "user"]
            self.assertEqual(len(users), 1)
            self.assertIn("strategy_metrics.json", users[0]["content"])
            bound = len(MODULE.SYSTEM.encode()) + len(users[0]["content"].encode()) + MODULE.FRAMING_TOKENS
            self.assertLessEqual(bound, self.config.max_input)
            self.assertEqual(call.kwargs["timeout"], 60)
        self.assertEqual(request.call_args_list[0].kwargs["payload"]["max_completion_tokens"], 2500)
        self.assertEqual(request.call_args_list[0].kwargs["payload"]["n"], 1)
        self.assertEqual(request.call_args_list[1].kwargs["payload"]["max_tokens"], 2500)
        self.assertTrue(self.issue.comments[0].startswith(MODULE.LEDGER_PREFIX))
        self.assertIn("OpenAI finding", self.issue.comments[-1])
        self.assertIn("Anthropic finding", self.issue.comments[-1])

    def test_transient_failure_still_calls_other_provider_once_and_posts_unavailable(self):
        self.clients["OpenAI"].review.side_effect = TimeoutError("private error content")
        self.assertFalse(self.run_review())
        for client in self.clients.values():
            client.review.assert_called_once()
        self.assertIn("review unavailable: OpenAI", self.issue.comments[-1])
        self.assertIn("Review finding", self.issue.comments[-1])
        self.assertNotIn("private error content", "\n".join(self.issue.comments))

    def test_both_fail_without_retries_and_post_unavailable(self):
        for client in self.clients.values():
            client.review.side_effect = RuntimeError("provider secret error")
        self.assertFalse(self.run_review())
        self.assertIn("review unavailable: OpenAI, Anthropic", self.issue.comments[-1])
        for client in self.clients.values():
            client.review.assert_called_once()

    def test_multibyte_materials_over_input_cap_make_zero_calls(self):
        (self.bundle / "ai_review_input.md").write_text("月报" * 3000, encoding="utf-8")
        self.assertFalse(self.run_review())
        self.assertIn("input cap", self.issue.comments[-1])
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_framing_and_combined_files_count_toward_input_cap(self):
        (self.bundle / "ai_review_input.md").write_text("a" * 8000)
        (self.bundle / "strategy_metrics.json").write_text("b" * 8000)
        self.assertFalse(self.run_review())
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_client_independently_rejects_oversized_prompt(self):
        with patch.object(MODULE, "request_json") as request:
            for provider in MODULE.MODELS:
                with self.assertRaises(MODULE.ReviewUnavailable):
                    MODULE.APIReviewer(provider, "fake").review("x" * 16000, self.config)
            request.assert_not_called()

    def test_output_usage_above_cap_is_unavailable(self):
        self.clients = {provider: MODULE.APIReviewer(provider, "fake") for provider in MODULE.MODELS}
        responses = [
            {"choices": [{"message": {"content": "invalid"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 2501}},
            {"content": [{"type": "text", "text": "invalid"}], "stop_reason": "max_tokens",
             "usage": {"input_tokens": 100, "output_tokens": 2500}},
        ]
        with patch.object(MODULE, "request_json", side_effect=responses) as request:
            self.assertFalse(self.run_review())
        self.assertEqual(request.call_count, 2)
        self.assertIn("review unavailable: OpenAI, Anthropic", self.issue.comments[-1])
        self.assertNotIn("invalid", self.issue.comments[-1])

    def test_budget_exhaustion_blocks_both_calls(self):
        self.issue = FakeIssue([ledger()])
        self.assertEqual(self.config.reservation(), Decimal("0.119625"))
        self.assertFalse(self.run_review())
        self.assertIn("monthly budget exhausted", self.issue.comments[-1])
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_duplicate_report_blocked_even_after_failed_run(self):
        self.clients["OpenAI"].review.side_effect = TimeoutError
        self.assertFalse(self.run_review())
        self.assertFalse(self.run_review())
        self.assertIn("already has a reservation", self.issue.comments[-1])
        for client in self.clients.values():
            client.review.assert_called_once()

    def test_unknown_report_not_replayed_after_utc_month_rollover(self):
        for failures in (("OpenAI",), ("OpenAI", "Anthropic")):
            with self.subTest(failures=failures):
                self.issue = FakeIssue()
                self.clients = {
                    name: Mock(review=Mock(return_value=json.dumps({
                        "verdict": "approve", "severity": "none", "summary": "Review finding",
                    }))) for name in ("OpenAI", "Anthropic")
                }
                for provider in failures:
                    self.clients[provider].review.side_effect = TimeoutError
                first = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
                self.issue.created_at = first.isoformat()
                self.assertFalse(MODULE.run_review(
                    self.bundle, self.config, self.issue, self.clients, first))
                next_month = datetime(2026, 11, 1, tzinfo=timezone.utc)
                self.issue.created_at = next_month.isoformat()
                self.assertFalse(MODULE.run_review(
                    self.bundle, self.config, self.issue, self.clients, next_month))
                for client in self.clients.values():
                    client.review.assert_called_once()
                reservations = [comment for comment in self.issue.comments
                                if comment.startswith(MODULE.LEDGER_PREFIX)]
                self.assertEqual(len(reservations), 1)

    def test_prior_month_same_report_reservation_blocks_another_issue(self):
        self.issue = FakeIssue([ledger(report_month="2026-10", month="2026-10")])
        self.issue.own_comments = Mock(return_value=[])
        next_month = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.assertFalse(MODULE.run_review(
            self.bundle, self.config, self.issue, self.clients, next_month))
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_reservation_history_scan_has_no_utc_month_cutoff(self):
        env = {"GITHUB_REPOSITORY": MODULE.REPOSITORY, "ISSUE_NUMBER": "20",
               "GITHUB_TOKEN": "fake"}
        responses = [{"labels": [{"name": "monthly-review"}]},
                     [ledger(report_month="2026-10", month="2026-10")], {}]
        with patch.object(MODULE, "request_json", side_effect=responses) as request:
            issue = MODULE.IssueComments(env)
            with self.assertRaises(MODULE.ReviewUnavailable):
                MODULE.reserve_budget(
                    issue, self.config, "2026-10", datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertNotIn("since=", request.call_args_list[1].args[0])
        self.assertEqual(request.call_count, 2)

    def test_bad_or_unreadable_ledger_fails_closed(self):
        for history in ([ledger(amount="NaN")], [ledger(amount="-1")]):
            self.issue = FakeIssue(history)
            self.assertFalse(self.run_review())
        self.issue = FakeIssue()
        self.issue.history = Mock(side_effect=TimeoutError)
        self.assertFalse(self.run_review())
        for client in self.clients.values():
            client.review.assert_not_called()
        self.assertIn("review unavailable", self.issue.comments[-1])

    def test_failed_reservation_write_makes_no_calls_and_never_retries_reservation(self):
        self.issue.post = Mock(side_effect=[TimeoutError, None])
        self.assertFalse(self.run_review())
        self.assertEqual(self.issue.post.call_count, 2)  # reservation, then an unavailable notice
        self.assertIn("review unavailable", self.issue.post.call_args.args[0])
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_prior_utc_month_reservations_do_not_consume_current_budget(self):
        self.issue = FakeIssue([ledger(amount="99", month="2026-09")])
        self.assertTrue(self.run_review())

    def test_missing_materials_post_unavailable_without_calls(self):
        (self.bundle / "strategy_metrics.json").unlink()
        self.assertFalse(self.run_review())
        self.assertIn("review unavailable", self.issue.comments[-1])
        for client in self.clients.values():
            client.review.assert_not_called()

    def test_missing_secrets_preflight_skips_without_printing_values(self):
        output = self.bundle / "output"
        env = {"GITHUB_OUTPUT": str(output), "OPENAI_API_KEY": "fake-private-value"}
        log = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(MODULE, "IssueComments", return_value=self.issue), \
                patch("sys.argv", ["review", "--preflight"]), patch.object(MODULE, "APIReviewer") as client, \
                redirect_stdout(log):
            self.assertEqual(MODULE.main(), 0)
        client.assert_not_called()
        self.assertEqual(output.read_text(), "ready=false\n")
        self.assertIn("ANTHROPIC_API_KEY", log.getvalue())
        self.assertNotIn("fake-private-value", log.getvalue() + "\n".join(self.issue.comments))
        self.assertIn("review unavailable", self.issue.comments[-1])

    def test_invalid_configuration_fails_closed(self):
        for env in ({"MONTHLY_REVIEW_MAX_INPUT_TOKENS": "0"},
                    {"MONTHLY_REVIEW_MAX_OUTPUT_TOKENS": "99999"},
                    {"MONTHLY_REVIEW_TIMEOUT_SECONDS": "0"},
                    {"MONTHLY_REVIEW_MONTHLY_BUDGET_USD": "NaN"},
                    {"MONTHLY_REVIEW_MONTHLY_BUDGET_USD": "0"}):
            with self.assertRaises(MODULE.ReviewUnavailable):
                MODULE.Config(env)

    def test_github_only_reads_and_posts_issue_comments_with_paginated_ledger(self):
        env = {"GITHUB_REPOSITORY": MODULE.REPOSITORY, "ISSUE_NUMBER": "20", "GITHUB_TOKEN": "fake"}
        responses = [{"labels": [{"name": "monthly-review"}]}, [ledger()] * 100, [], {}]
        with patch.object(MODULE, "request_json", side_effect=responses) as request:
            issue = MODULE.IssueComments(env)
            self.assertEqual(len(issue.history("2026-10")), 100)
            issue.post("review unavailable")
        calls = request.call_args_list
        self.assertIn("page=2", calls[2].args[0])
        for call in calls[:-1]:
            self.assertNotIn("payload", call.kwargs)
        self.assertTrue(calls[-1].args[0].endswith("/issues/20/comments"))
        self.assertEqual(calls[-1].kwargs["payload"], {"body": "review unavailable"})

    def test_issue_comments_redact_known_credentials(self):
        env = {"GITHUB_REPOSITORY": MODULE.REPOSITORY, "ISSUE_NUMBER": "20",
               "GITHUB_TOKEN": "fake-github-credential", "OPENAI_API_KEY": "fake-provider-credential"}
        with patch.object(MODULE, "request_json", side_effect=[{"labels": [{"name": "monthly-review"}]}, {}]) as request:
            issue = MODULE.IssueComments(env)
            issue.post("fake-github-credential fake-provider-credential")
        self.assertEqual(request.call_args.kwargs["payload"]["body"], "[redacted] [redacted]")

    @unittest.skipUnless(hasattr(signal, "SIGALRM"), "Linux/macOS wall-clock deadline")
    def test_wall_clock_timeout_interrupts_slow_response_and_restores_alarm(self):
        with self.assertRaises(TimeoutError):
            with MODULE.deadline(1):
                time.sleep(2)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
