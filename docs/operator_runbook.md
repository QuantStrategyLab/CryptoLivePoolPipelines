# Operator Runbook

## Scope

This runbook covers the production release path for `CryptoSnapshotPipelines`.

Primary production outputs:

- `data/output/latest_universe.json`
- `data/output/latest_ranking.csv`
- `data/output/live_pool.json`
- `data/output/live_pool_legacy.json`
- `data/output/artifact_manifest.json`
- `data/output/release_manifest.json`
- `data/output/release_status_summary.json`
- `data/output/release_status_summary.md`
- `data/output/monthly_review.json`
- `data/output/monthly_review.md`
- `data/output/monthly_telegram.txt`
- `data/output/monthly_report_bundle/monthly_report_bundle.json`
- `data/output/monthly_report_bundle/ai_review_input.md`
- `data/output/monthly_report_bundle/job_summary.md`

Primary publish targets:

- GCS current pointers under `crypto-leader-rotation/current`
- GCS versioned release objects under `crypto-leader-rotation/releases/<version>`
- Firestore `strategy/CRYPTO_LEADER_ROTATION_LIVE_POOL`

The GCS prefix, Firestore document, and `source_project` value intentionally
remain in the `crypto-leader-rotation` v1 artifact namespace for downstream
compatibility.

## Research Path Vs Production Path

Production path:

- `scripts/download_history.py`
- `scripts/build_live_pool.py`
- `scripts/validate_release_contract.py`
- `scripts/publish_release.py`
- `scripts/write_release_heartbeat.py`

Research-only / non-publish path:

- `scripts/run_research_backtest.py`
- `scripts/run_walkforward_validation.py`
- `scripts/compare_external_data.py`
- `scripts/sweep_external_data_profiles.py`
- `scripts/run_monthly_shadow_build.py`

Rules:

- Do not treat shadow outputs, external-data experiments, or research summaries as publish-ready production artifacts.
- Only `core_major` build outputs that pass contract validation should be published to downstream systems.
- If a manual run uses `--as-of-date` for historical investigation, treat it as replay unless you intentionally publish with `--allow-stale`.

## Upstream Reporting Responsibilities

This repository owns the monthly reporting and publish-status summary layer for the upstream release.

Operator-facing summary entrypoints:

- `scripts/run_release_status_summary.py` for the canonical release-status summary built from the current official artifacts
- `scripts/run_monthly_review_briefing.py` for the monthly review package; it requires same-cycle shadow outputs so review evidence always includes official and challenger coverage
- `scripts/run_monthly_build_telegram.py` for the optional short Telegram health notification or local preview text
- `scripts/run_monthly_report_bundle.py` for the standard monthly report bundle used by Actions artifacts and AI review handoff
- `scripts/write_release_heartbeat.py` for the lightweight logs-branch heartbeat record
- Monthly live-pool ordering uses a deterministic tie-break: `final_score`, then `confidence`, then `liquidity_stability`, then `avg_quote_vol_180`, then `symbol`

Boundary rules:

- Downstream execution systems should consume the validated release artifacts, not recreate the monthly report layer.
- Research reports and shadow-track diagnostics stay upstream and are not part of the minimum downstream execution contract.
- Telegram messages from this repository are operational release notifications, not trade execution alerts.

## Monthly Codex Remediation

The monthly publish workflow creates a `monthly-review` issue, then dispatches `CryptoCodexAuditBridge` as the automated review and remediation path. The bridge owns provider selection through `SELFHOSTED_CODEX_REVIEW_PROVIDER`: `codex` runs self-hosted Codex and may open focused PRs, `openai` posts an API review comment only, and `auto` falls back to OpenAI review when Codex fails and the bridge has `OPENAI_API_KEY`.

If the bridge dispatch fails, the monthly publish workflow fails loudly. The older source-local `ai_review.yml` workflow remains only as a manual compatibility path.

The monthly optimization planner may still create repo-scoped follow-up issues after AI review. For `CryptoSnapshotPipelines`, low-risk non-experiment tasks marked `[auto-pr-safe]` are queued to the self-hosted VPS ccbot/Codex runner with the `codex-bridge` label. GitHub-hosted Claude Action remains only a manual-dispatch fallback; normal automated code remediation should run through ccbot/Codex.

Codex remediation PRs must use branch `codex/monthly-optimization-issue-<issue-number>`, include `<!-- auto-optimization-pr:issue-<issue-number> -->` in the PR body, and start as draft. The auto-merge workflow only merges after CI passes, the PR is ready for review, `auto-merge-ok` is present, task-level auto-merge eligibility is recorded, and changed files stay outside guarded selector/config paths.

If CI fails on a Codex remediation PR, or a reviewer requests changes, `Codex PR Feedback` comments the failure or review summary back to the source `codex-bridge` issue. The issue update lets the VPS bridge dispatch Codex again to fix the same PR branch. The workflow permits up to three automatic feedback rounds, then removes `codex-bridge` and leaves the issue for human review.

## Standard Monthly Flow

1. Refresh or verify local data:

```bash
.venv/bin/python scripts/download_history.py --top-liquid 90 --force-exchange-info
```

2. Build the production pool:

```bash
.venv/bin/python scripts/build_live_pool.py --universe-mode core_major
```

3. Run explicit contract validation:

```bash
.venv/bin/python scripts/validate_release_contract.py --mode core_major --expected-pool-size 5 --require-manifest --require-artifact-manifest
```

4. Run publish preflight without external writes:

```bash
.venv/bin/python scripts/publish_release.py --dry-run --mode core_major
```

5. Generate the canonical release-status summary:

```bash
.venv/bin/python scripts/run_release_status_summary.py
```

Before the review package is generated, confirm the monthly shadow build has produced both
`data/output/monthly_shadow_build_summary.json` and
`data/output/shadow_candidate_tracks/track_summary.csv`. The hosted monthly publish
workflow runs this shadow build automatically before the review step.

6. Generate the monthly review package:

```bash
.venv/bin/python scripts/run_monthly_review_briefing.py
```

7. Render the Telegram preview text without sending:

```bash
.venv/bin/python scripts/run_monthly_build_telegram.py --print-only --output-path data/output/monthly_telegram.txt
```

8. Assemble the monthly report bundle:

```bash
.venv/bin/python scripts/run_monthly_report_bundle.py
```

9. Only after steps 1-8 pass, run the real publish path through the workflow or a controlled manual execution.

## Preflight Checklist

- `requirements-lock.txt` is present and matches the intended release dependency set.
- Local artifacts are non-empty and pass `scripts/validate_release_contract.py`.
- `live_pool.json`, `live_pool_legacy.json`, and `release_manifest.json` agree on `as_of_date`, `version`, `mode`, `pool_size`, and `source_project`; `artifact_manifest.json` agrees on `as_of_date`, `version`, `mode`, `source_project`, and `symbol_count`.
- `GCP_PROJECT_ID`, `GCS_BUCKET`, `FIRESTORE_COLLECTION`, and `FIRESTORE_DOCUMENT` are set correctly for real publish.
- Historical backfills use `--allow-stale` explicitly; do not silently publish stale artifacts.

## Common Failure Modes

### Missing or malformed artifacts

Symptoms:

- `validate_release_contract.py` exits non-zero
- `publish_release.py` fails during preflight

Actions:

- Re-run `scripts/build_live_pool.py`
- Inspect `data/output/latest_universe.json`, `latest_ranking.csv`, `live_pool.json`, `live_pool_legacy.json`, and `artifact_manifest.json`
- Confirm `pool_size`, `symbols`, `symbol_map`, `version`, and `source_project` are present and aligned

### Stale artifacts

Symptoms:

- Contract validation reports outputs are older than the allowed age window

Actions:

- Prefer rebuilding from refreshed raw data
- If you are intentionally replaying an older month, use `--allow-stale` and record that the run is historical
- Do not treat stale output as a healthy production release

### Missing publish configuration

Symptoms:

- Preflight errors for `GCP_PROJECT_ID`, `GCS_BUCKET`, Firestore collection, or Firestore document

Actions:

- Fix the missing workflow variable first
- Re-run `scripts/publish_release.py --dry-run --mode core_major`
- Only retry real publish after dry-run is clean

### Empty pool or ranking mismatch

Symptoms:

- `pool_size` mismatch
- live pool symbols not found in ranking
- live pool symbols are not a subset of latest universe

Actions:

- Stop the release
- Review the latest build inputs and `config/default.yaml`
- Rebuild and validate before any downstream sync

## Minimal Rollback

Use rollback only when the newest publish is clearly bad or malformed.

1. Identify the last known good version from:

- Firestore document history
- GCS `crypto-leader-rotation/releases/<version>/`
- the last good `data/output/release_manifest.json`

2. Restore the five canonical artifacts from that version into `data/output/`:

- `latest_universe.json`
- `latest_ranking.csv`
- `live_pool.json`
- `live_pool_legacy.json`
- `artifact_manifest.json`

3. Validate the restored payload locally:

```bash
.venv/bin/python scripts/validate_release_contract.py --mode core_major --expected-pool-size 5 --require-artifact-manifest
```

4. Regenerate the manifest and verify publish preflight:

```bash
.venv/bin/python scripts/publish_release.py --dry-run --mode core_major --allow-stale
```

5. Re-publish the restored version to GCS / Firestore through the controlled release path.

Rollback note:

- `--allow-stale` is expected during rollback because the restored version is historical.
- Record the rollback version and reason in the operator log or release notes.

## Post-Release Checks

- Confirm `release_status_summary.json` reports `status=ok` for the published month.
- Confirm `data/output/monthly_report_bundle/job_summary.md` matches the released month and includes the expected bundle file list.
- Confirm the Actions run uploaded a `monthly-report-<as_of_date>` artifact for download.
- Confirm Firestore `strategy/CRYPTO_LEADER_ROTATION_LIVE_POOL` contains the expected `version`, `mode`, `symbols`, and `source_project`.
- Confirm GCS current pointers and versioned objects exist for the same version.
- Confirm downstream consumers are reading the new version without falling back to degraded sources.
