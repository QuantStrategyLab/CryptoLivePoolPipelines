# CryptoLivePoolPipelines

Pipeline that selects and publishes the monthly crypto live-pool used by QuantStrategyLab crypto strategies.

English | [简体中文](README.zh-CN.md)

## Status

Experimental. Use at your own risk. This project is not financial advice and
does not guarantee any trading result. Its outputs are research evidence and
release artifacts, not trading instructions. It does not place trades.

## What this is

CryptoLivePoolPipelines owns the monthly crypto live-pool: candidate ranking,
pool membership and order, shadow validation tracks, and the release artifacts
that downstream strategy code reads. It consumes market data and QuantPlatformKit
helpers and writes versioned artifacts under a documented contract. It is the
upstream evidence and release layer for crypto strategy runtimes.

## What this is not

It does not place trades and is not an execution platform. It does not decide
strategy logic; the trading logic lives in `CryptoStrategies`. Downstream systems
must not rebuild the pool from local indicators instead of the published
artifacts.

## Quick start

Requires Python 3.11 or newer.

Create the project virtual environment and install the pinned runtime
requirements into it:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-lock.txt
```

Run the unit tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

Build and check a monthly shadow release locally. The `make` targets invoke
`.venv/bin/python` (see [`Makefile`](Makefile)), so create `.venv` first as
shown above:

```bash
make monthly-shadow-build
make monthly-shadow-check
```

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PUBLISH_ENABLED` | No | `false` | Allow publishing release artifacts. |
| `PUBLISH_MODE` | No | `core_major` | Publish mode for release objects. |
| `GCS_BUCKET` | No | none | Bucket for published release objects. |
| `CLOUD_PROJECT_ID` | No | none | Google Cloud project for storage and Firestore. |
| `FIRESTORE_COLLECTION` | No | `strategy` | Collection that receives the current-pointer document. |
| `FIRESTORE_DOCUMENT` | No | `CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL` | Document that receives the current-pointer payload. |
| `UPLOAD_CURRENT_POINTER` | No | `true` | Update the current-pointer pointer after publish. |
| `TELEGRAM_BOT_TOKEN` | No | none | Bot token for monthly notifications. |
| `GLOBAL_TELEGRAM_CHAT_ID` | No | none | Chat that receives monthly notifications. |
| `NOTIFY_LANG` | No | `en` | Notification language. |
| `REVIEW_DEADMAN_THRESHOLD` | No | `3` | Failed or skipped cycles before the dead-man alert. |
| `QSL_DISPLAY_NAMES_JSON` | No | none | Display-name overrides for notifications. |

Pipeline inputs and defaults live in [`config/default.yaml`](config/default.yaml).
The publish bucket and project identifiers are unset by default; provide them
through the environment variables above or your config file.

## Monthly review automation

After a successful monthly publish, a separate review-only job reads the
released monthly report and `strategy_metrics.json` from the same run, issues one
OpenAI and one Anthropic request, and posts the single-turn result as a comment
on the existing `monthly-review` issue. It does not trigger the retired
AIAuditBridge Codex Audit, does not use the Codex or Cursor CLI, and does not
modify code or trading configuration.

The default per-request input cap is 16,000 tokens, the output cap is 2,500
tokens, the timeout is 60 seconds, retries are zero, and the natural-month budget
is 1 USD. Input is counted conservatively as UTF-8 byte lengths plus fixed
message overhead, so oversized input is skipped rather than truncated. Missing
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY` skips the API job and posts a
`review unavailable` comment. See the
[operator runbook](docs/operator-runbook.md#monthly-review-only-api-audit) for
the configuration and budget rules.

Production publish targets such as the GCP project, GCS bucket, and Firestore
document must be read from GitHub variables. This repository no longer keeps a
source-local `ai_review.yml`; the new API review job lives in
`monthly_publish.yml`. It reads the repository and the published report, writes
issue comments, archives the verdict, and updates review state. It marks the
review issue `auto-verified` and closes it when both API reviews approve; when a
review flags a concern, it submits one bounded deeper advisory review if an
approved AIGateway route is configured, and closes the issue unless that review
confirms a real problem. It does not edit code, change live configuration, or
authorize promotion or trading.

## Repository layout

```
src/        pipeline, ranking, validation, and publish code
scripts/    operator scripts and monthly jobs
config/     pipeline configuration
data/       local and generated output
docs/       contracts, roadmap, and operator runbook
tests/      unit and contract tests
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI assistants and maintainers also follow [AGENTS.md](AGENTS.md).

## Security

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.
Keep credentials, private account details, and generated data out of Git.

## License

MIT. See [LICENSE](LICENSE).
