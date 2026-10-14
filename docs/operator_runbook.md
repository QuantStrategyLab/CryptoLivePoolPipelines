# Operator Runbook

## Scope

This runbook covers the production release path for `CryptoLivePoolPipelines`.

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

- GCS current pointers under `crypto-live-pool-pipelines/current`
- GCS versioned release objects under `crypto-live-pool-pipelines/releases/<version>`
- Firestore `strategy/CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL`

The GCS prefix, Firestore document, and `source_project` value use the
`crypto-live-pool-pipelines` v1 artifact namespace.

This repository owns the monthly live-pool membership, ranking, and published
symbol order. Downstream runtime repositories should consume the validated
artifact contract and preserve that order when passing the pool into strategy
code.

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

This repository owns the monthly reporting and publish-status summary layer for the upstream release. These summaries are background evidence for artifact or content health, not a proxy for any AiGateway online service health, and they do not authorize automated trading or unconditional publish decisions.

Operator-facing summary entrypoints:

- `scripts/run_release_status_summary.py` for the canonical release-status summary built from the current official artifacts
- `scripts/run_monthly_review_briefing.py` for the monthly review package; it requires same-cycle shadow outputs so review evidence always includes official and challenger coverage
- `scripts/run_monthly_build_telegram.py` for the optional short Telegram health notification or local preview text
- `scripts/run_monthly_report_bundle.py` for the standard monthly report bundle used by Actions artifacts and AI review handoff
- `scripts/write_release_heartbeat.py` for the lightweight logs-branch heartbeat record
- Monthly live-pool ordering uses a deterministic tie-break: `final_score`, then `confidence`, then `liquidity_stability`, then `avg_quote_vol_180`, then `symbol`
- Monthly review evidence includes the latest official/challenger track composition, overlap against the official pool, next-candidate boundary, and the deterministic tie-break order.

Boundary rules:

- Downstream execution systems should consume the validated release artifacts, not recreate the monthly report layer.
- Downstream systems should not rerank or rebuild the monthly live pool from local runtime indicators.
- Research reports and shadow-track diagnostics stay upstream and are not part of the minimum downstream execution contract.
- Telegram messages from this repository are operational release notifications, not trade execution alerts.

## Monthly review-only API audit

The `Monthly Publish` schedule remains `15 3 1 * *` (03:15 UTC on the first of
each month; 11:15 in Singapore). After successful publication on `main` and creation
of the existing `monthly-review` issue, a hosted preflight checks the existing
`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` secrets. It never creates or prints secrets.
If either is unavailable to the workflow, preflight logs the missing names and
posts `review unavailable`; the `monthly-dual-api-review` job is skipped. Repository
and organization secret visibility can differ, so runtime preflight is authoritative.

The isolated hosted review job downloads the monthly artifact from the same run.
It reads only `monthly_report_bundle.json`, `ai_review_input.md`, and
`strategy_metrics.json`, and issues one OpenAI Chat Completions request and one
Anthropic Messages request. Both are single-turn, with no tools, streaming,
continuations, fallback providers, SDK retries, or CLI agents. A failure in one
provider still permits the other provider's single request; the final comment marks
the failed review `review unavailable` and the job fails. Missing/oversized materials
or a failed budget check make zero API requests and post the same unavailable notice.
GitHub write failures are not retried because the write may already have succeeded.

| GitHub variable | Default | Accepted range |
| --- | --- | --- |
| `MONTHLY_REVIEW_MAX_INPUT_TOKENS` | `16000` per request | 1,024–128,000 |
| `MONTHLY_REVIEW_MAX_OUTPUT_TOKENS` | `2500` per request | 128–8,192 |
| `MONTHLY_REVIEW_TIMEOUT_SECONDS` | `60` per request | 1–120 seconds |
| `MONTHLY_REVIEW_MONTHLY_BUDGET_USD` | `1.00` | greater than 0, at most 100 USD |

Input accounting uses a conservative upper bound: the UTF-8 byte lengths of the
system instructions and both published materials, including their headings, plus
256 tokens for fixed message framing. This can reject a report whose actual token
count would be smaller; it never silently truncates evidence or makes a separate
token-count API call. Output limits are sent as OpenAI `max_completion_tokens`
(including reasoning) and Anthropic `max_tokens`. Reported usage is also checked;
missing usage, over-cap usage, or a truncated answer is unavailable. A wall-clock
deadline covers each HTTP request and response read. The review job has a twenty-minute
overall deadline. Retries are fixed at zero to preserve the exactly-two-request limit.

Models and standard-service rates are fixed in the review script:
[`gpt-5.4-mini-2026-03-17`](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
at $0.75/$4.50 per million input/output tokens, and
[`claude-sonnet-4-6`](https://platform.claude.com/docs/en/models/sonnet-4-6/overview)
at $3/$15. Defaults reserve **$0.119625** for both calls before either starts,
including a 10% margin for regional processing charges (API token spend, excluding taxes).
Model/rate changes require a reviewed code change; update rates when provider pricing
changes. This budget covers this workflow's calls, not other activity on the API keys.

The durable ledger consists exclusively of reservation comments on existing review
issues. Runs are serialized by `monthly-dual-api-review-budget`. Before reserving,
the script reads every page of repository issue comments since the UTC month began,
counts current-month reservations authored by `github-actions[bot]`, and rejects
unreadable/incomplete/malformed history, insufficient balance, or an existing
reservation for the same report month. Reservations are never refunded, even after
timeout, cancellation, or partial failure; a failed reservation write makes no API
requests. Do not delete or edit reservation comments, since they preserve the budget
and rerun record. A rerun of an already reserved report is skipped rather than charged
again. If a durable loop verdict already exists, reruns resume only that loop and
read the original Dot task; they never charge the API pair again.

The review job has `contents: read`, `actions: read`, `issues: write`, and
`id-token: write`, with checkout credentials disabled. OIDC is solely for the
current workload's already approved AIGateway project; it does not add an IAM or
service grant. Remote writes are review state/comments, labels, and issue closure.
The original publisher still owns publication and heartbeat operations. This audit
does not dispatch AIAuditBridge, fix code, create PRs, mutate releases, change live
configuration, or authorize promotion/trading. CryptoStrategies consumes the upstream
pool; these selector/shadow metrics do not prove downstream strategy or live results.
A successful publisher or an open review issue proves material preparation, not
completed API review. Check the separate review job and its comment for both
provider results. The retired `CODEX_AUDIT_*` settings do not enable remediation.

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
- GCS `crypto-live-pool-pipelines/releases/<version>/`
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
- Confirm Firestore `strategy/CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL` contains the expected `version`, `mode`, `symbols`, and `source_project`.
- Confirm GCS current pointers and versioned objects exist for the same version.
- Confirm downstream consumers are reading the new version without falling back to degraded sources.

### Unattended review disposition

Each API must return strict JSON with `verdict` (`approve` or `flag`), `severity`
(`none`, `data_integrity`, `risk_limit`, `missing_stale_inputs`), and an evidence-based
English `summary`. Malformed, missing, partial, or truncated responses never pass.
Model-generated mentions are neutralized. Two approvals archive the verdict JSON,
apply `auto-verified`, and close the existing monthly-review issue. This closes the
review record; it does not approve a release or override deterministic gates.

Any flag submits at most one deeper Dot task with the frozen materials, via the
current workflow's existing AIGateway grant. Existing `AI_SERVICE_URL`,
`AI_SERVICE_AUDIENCE`, `AI_SERVICE_PROFILE`, and `AI_SERVICE_MODEL` variables select
that route. No cross-repository credential or grant is borrowed. Submission intent
is saved in bot-authored issue comments before POST; reruns only GET the original
task. An ambiguous POST without a task ID stays blocked. Dot's strict confirmation
is posted back; only confirmation of a real problem mentions the owner. Dismissal
archives the verdict and closes the review issue. API budget caps remain unchanged;
Dot uses only existing subscription task admission and limits, with no paid fallback.

Severe consensus requires both reviewers to flag the same severe category. Only
an existing actuator bound to this cycle may pause rebalancing, holding positions
and creating no orders. **This pipeline has no such bound actuator**: the loop
reports the gap and notifies the owner, without building or changing trading logic.
No finding or AI response can edit code, configuration, settings, orders or servers.

`Monthly Review Dead-man` runs independently after Monthly Publish and daily. It
counts failed, skipped, cancelled, timed-out and missing review jobs, and missing
natural monthly cycles after November 2026. `REVIEW_DEADMAN_THRESHOLD` defaults to
`3` (range 1–24); success resets the streak. Alerts are bot-authored, deduplicated
GitHub issues mentioning `REVIEW_OWNER_LOGIN` (default `Pigbibi`). A monitor API
failure fails the monitor visibly rather than claiming health. Verdict artifacts
are retained for 90 days; durable issue records are retained without expiry.

Current operational gaps: both API secret names must already be available to this
repository; the deployed grant template does not establish monthly-publisher Dot
admission. Without a route/grant, flags remain `escalation_unavailable` and count
toward the dead-man threshold. This change creates neither secrets nor grants and
does not run production publication or a live model acceptance test.
