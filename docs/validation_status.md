# Validation Status

This document tracks the current frozen production decision and what still remains experimental.

## Frozen Default

The repository is now frozen around this production default:

- `Production v1`
- data source: `Binance Spot only`
- live universe mode: `core_major`
- publish cadence: `monthly`
- default outputs:
  - `latest_universe.json`
  - `latest_ranking.csv`
  - `live_pool.json`
  - `live_pool_legacy.json`
  - `artifact_manifest.json`

This is the only path that should be treated as the formal production baseline.

The external-data branch is retained, but only as:

- research
- comparison
- quality hardening
- experimental validation

It is not enabled by default and it is not part of the default production publish chain.

## Current Strategy Validation Snapshot

Recommended baseline as of `2026-03-14`:

- research universe mode: `broad_liquid`
- production live mode: `core_major`
- production data source: `Binance-only`
- publish cadence: `monthly`
- validation environment: `.venv/bin/python`
- validation method: `purged walk-forward + overlap aggregation + monthly live-pool shadow`

Recommended purged research summary for `final_score`:

- CAGR: `19.38%`
- Annualized Volatility: `65.04%`
- Sharpe: `0.5986`
- Max Drawdown: `-85.56%`
- Turnover: `12.17`

Recommended purged walk-forward summary:

- windows: `31`
- mean H30 Precision@N: `0.1822`
- mean H60 Precision@N: `0.1867`
- mean H90 Precision@N: `0.1902`
- mean H30 Leader Capture: `0.1442`
- mean H60 Leader Capture: `0.1559`
- mean H90 Leader Capture: `0.1784`
- mean window Sharpe: `0.6506`
- mean window Turnover: `17.78`

Monthly live-pool shadow summary:

- evaluation dates: `64`
- cadence: `monthly`
- live pool size: `5`
- mean pool churn: `0.4159`
- mean H30 pool precision: `0.1742`
- mean H60 pool precision: `0.1803`
- mean H90 pool precision: `0.1667`
- mean H30 leader capture inside pool: `0.2581`
- mean H60 leader capture inside pool: `0.2951`
- mean H90 leader capture inside pool: `0.3000`

Methodology hardening summary:

- previous walk-forward validation built forward labels before window slicing and did not purge train-tail rows
- that allowed some training rows near `train_end` to use future prices that extended into the following test period
- previous validation also averaged duplicate predictions created by overlapping test windows, which is smoother than the real live path
- purged walk-forward is now the recommended baseline
- monthly live-pool shadow validation is now available and better matches the actual exported monthly `5`-name artifact
- plain `python3` in this workspace may lack required ML dependencies, so `.venv/bin/python` is the intended validation entrypoint

Legacy historical baseline, retained for context only and not directly comparable:

- research CAGR: `47.91%`
- research Sharpe: `0.9262`
- mean walk-forward H60 Precision@N: `0.2200`
- mean walk-forward H60 Leader Capture: `0.1867`
- mean monthly shadow H60 pool precision: `0.1934`
- mean monthly shadow H60 leader capture: `0.3115`

Interpretation:

- the project is now usable as an upstream production pool publisher
- validation is now methodologically stricter and more realistic than the legacy baseline
- the default production path is intentionally frozen around Binance-only stability
- current priority should remain monthly refresh discipline and stable contract publishing rather than further production-path experimentation
- downstream consumers should validate the exported live-pool contract using the stable fields documented in `docs/integration_contract.md`

Report locations for the hardened baseline:

- `data/reports/performance_summary.csv`
- `data/reports/leader_metrics.csv`
- `data/reports/walkforward_validation_summary.csv`
- `data/reports/monthly_live_pool_shadow_detail.csv`
- `data/reports/monthly_live_pool_shadow_summary.csv`
- `data/output/shadow_releases/release_index.csv` for local downstream shadow replay when generated

These report files are local generated artifacts under `data/reports/` and are not committed to git by default.

Additional shadow-candidate operator output, when generated:

- `data/output/shadow_candidate_tracks/track_summary.csv`

Shadow candidate status:

- baseline remains the official production reference
- `challenger_topk_60` is tracked only as a shadow-production candidate
- shadow candidate artifacts are additive and do not replace the default live build or publish path
- monthly operator entrypoint: `.venv/bin/python scripts/run_monthly_shadow_build.py`

## Publish Chain Validation Completed

Validated in-repo:

- `scripts/build_live_pool.py` produces the default `Production v1` live output
- `scripts/publish_release.py --dry-run` builds a correct production release manifest
- `scripts/validate_release_contract.py --require-artifact-manifest` validates the profile-aware artifact contract
- `scripts/write_release_heartbeat.py` writes a small logs-branch heartbeat file
- GitHub Actions workflow YAML parses correctly
- release versioning, GCS object keys, and Firestore payload layout are consistent
- `release_manifest.json` and heartbeat payloads are internally consistent

Validated artifacts:

- `data/output/latest_universe.json`
- `data/output/latest_ranking.csv`
- `data/output/live_pool.json`
- `data/output/live_pool_legacy.json`
- `data/output/artifact_manifest.json`
- `data/output/release_manifest.json`
- `data/output/heartbeat/monthly/<version>.json`

## External Data Preparation Validation Completed

Validated in-repo:

- provider abstraction exists
- pre-Binance and alternate-exchange merge logic exists
- duplicate-date resolution and source priority work in mock tests
- merged series remain monotonic and deduplicated
- optional market-cap metadata loader works in mock mode

Current external-data conclusion:

- external-data is now close enough to remain worth tracking
- the best experimental profile is `external_data_core_only_no_doge`
- but it still does not win clearly enough across the full `30 / 60 / 90` walk-forward objective set to replace `Production v1`
- therefore external-data remains experimental only

## Not Finished Yet

The following are intentionally not complete yet:

1. real GCS upload validation with production credentials
2. real Firestore write validation with production credentials
3. first successful GitHub Actions `workflow_dispatch` run in the hosted environment
4. rollback drill using a previous published version
5. promotion of external-data from experimental to production, if future validation justifies it
6. model-quality improvement work
7. LightGBM environment hardening on all target runtimes

## Pending Optimization Items

These are known next-step improvements, but they are not blockers for the current upstream publishing scope:

1. improve leader capture and precision in the broader research universe
2. revisit rule / ML blending after the universe split settles
3. tighten download ranking further to reduce very young hype-asset overrepresentation
4. validate `core_major` stability across more monthly snapshots
5. continue comparing Binance-only and external-history builds in the experimental track only
6. add a non-destructive rollback helper for release manifests and current pointers

## Release Blockers Still Remaining

Before relying on the monthly publisher in production, the remaining must-do checks are:

1. configure repository Secrets / Variables correctly
2. verify service-account permissions for Storage and Firestore
3. run one real publish from GitHub Actions
4. confirm the `logs` branch heartbeat push succeeds
5. test downstream consumer reading the published contract without changing strategy logic
