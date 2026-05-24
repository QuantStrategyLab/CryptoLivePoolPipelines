from __future__ import annotations

import unittest

from scripts.build_monthly_optimization_plan import build_plan, render_summary_markdown


class BuildMonthlyOptimizationPlanTests(unittest.TestCase):
    def test_build_plan_groups_in_scope_actions_by_owner_repo(self) -> None:
        upstream_review = {
            "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
            "review_kind": "upstream_selector",
            "source_issue": {"number": 11, "title": "Monthly Report Review: 2026-04-01", "url": "https://github.com/a/b/issues/11"},
            "risk_level": "medium",
            "production_recommendation": "research_only",
            "summary": "Need more challenger evidence.",
            "recommended_actions": [
                {
                    "owner_repo": "CryptoSnapshotPipelines",
                    "title": "Add challenger breadth check",
                    "risk_level": "low",
                    "auto_pr_safe": True,
                    "experiment_only": True,
                    "summary": "Improve evidence coverage.",
                }
            ],
        }

        plan = build_plan(upstream_review)

        self.assertEqual(plan["highest_review_risk"], "medium")
        self.assertIn("CryptoSnapshotPipelines", plan["repo_action_summary"])
        self.assertEqual(len(plan["safe_auto_pr_candidates"]), 1)
        self.assertEqual(len(plan["experiment_candidates"]), 1)

    def test_build_plan_keeps_non_snapshot_actions_out_of_scope(self) -> None:
        upstream_review = {
            "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
            "review_kind": "upstream_selector",
            "source_issue": {"number": 11, "title": "Monthly Report Review: 2026-04-01", "url": "https://github.com/a/b/issues/11"},
            "risk_level": "low",
            "production_recommendation": "keep_production_as_is",
            "summary": "Upstream is stable.",
            "recommended_actions": [
                {
                    "owner_repo": "CryptoStrategies",
                    "title": "Add selector report note",
                    "risk_level": "low",
                    "auto_pr_safe": True,
                    "experiment_only": False,
                    "summary": "Document upstream selector evidence.",
                },
                {
                    "owner_repo": "BinancePlatform",
                    "title": "Check DCA and rotation eligibility gates",
                    "risk_level": "low",
                    "auto_pr_safe": True,
                    "experiment_only": False,
                    "summary": "Downstream runtime follow-up should not be fanout from this planner.",
                },
            ],
        }

        plan = build_plan(upstream_review)

        self.assertNotIn("CryptoStrategies", plan["repo_action_summary"])
        self.assertNotIn("BinancePlatform", plan["repo_action_summary"])
        self.assertEqual(
            sorted(action["owner_repo"] for action in plan["out_of_scope_actions"]),
            ["BinancePlatform", "CryptoStrategies"],
        )
        self.assertEqual(len(plan["safe_auto_pr_candidates"]), 0)

    def test_render_summary_markdown_mentions_source_reviews_and_repos(self) -> None:
        plan = {
            "highest_review_risk": "medium",
            "safe_auto_pr_candidates": [{}, {}],
            "experiment_candidates": [{}],
            "human_review_required": [{}],
            "source_reviews": [
                {
                    "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
                    "risk_level": "medium",
                    "production_recommendation": "research_only",
                    "summary": "Need more evidence.",
                    "source_issue": {"title": "Monthly Report Review: 2026-04-01", "url": "https://github.com/a/b/issues/11"},
                    "run_url": "https://github.com/a/b/actions/runs/1",
                }
            ],
            "repo_action_summary": {
                "CryptoSnapshotPipelines": {
                    "actions": [
                        {
                            "risk_level": "low",
                            "title": "Add challenger breadth check",
                            "summary": "Improve evidence coverage.",
                            "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
                            "source_issue_number": 11,
                            "auto_pr_safe": True,
                            "experiment_only": True,
                        }
                    ]
                }
            },
            "operator_focus": ["QuantStrategyLab/CryptoSnapshotPipelines: Need more evidence."],
        }

        markdown = render_summary_markdown(plan)

        self.assertIn("# Monthly Optimization Planner", markdown)
        self.assertIn("QuantStrategyLab/CryptoSnapshotPipelines", markdown)
        self.assertIn("Add challenger breadth check", markdown)
        self.assertIn("Operator Focus", markdown)

    def test_render_summary_mentions_out_of_scope_actions(self) -> None:
        plan = {
            "highest_review_risk": "low",
            "safe_auto_pr_candidates": [],
            "experiment_candidates": [],
            "human_review_required": [],
            "source_reviews": [
                {
                    "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
                    "risk_level": "low",
                    "production_recommendation": "keep_production_as_is",
                    "summary": "Stable.",
                    "source_issue": {"title": "Monthly Report Review: 2026-04-01", "url": "https://github.com/a/b/issues/11"},
                    "run_url": "https://github.com/a/b/actions/runs/1",
                }
            ],
            "repo_action_summary": {},
            "operator_focus": [],
            "out_of_scope_actions": [
                {
                    "risk_level": "low",
                    "owner_repo": "BinancePlatform",
                    "title": "Check downstream gates",
                    "source_repo": "QuantStrategyLab/CryptoSnapshotPipelines",
                    "source_issue_number": 11,
                }
            ],
        }

        markdown = render_summary_markdown(plan)

        self.assertIn("Out-of-Scope Actions", markdown)
        self.assertIn("BinancePlatform", markdown)


if __name__ == "__main__":
    unittest.main()
