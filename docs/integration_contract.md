# Integration Contract


## 中文摘要

- 用途：本文档围绕 `Integration Contract`，用于理解 `CryptoLivePoolPipelines` 的配置、运行、部署、研究或验收边界。
- 主要覆盖：`Canonical Downstream Files`、``artifact_manifest.json``、``live_pool_legacy.json``、``live_pool.json``、`Firestore Contract`。
- 阅读顺序：先确认边界、输入输出和权限要求，再执行文档里的命令、CI、dry-run、发布或切换步骤。
- 风险提示：涉及实盘、密钥、权限、Cloud Run、交易所或券商 API 的变更，必须先在测试环境或 dry-run 验证；不要只凭示例直接修改生产。
- 英文正文保留更完整的命令、字段名和配置键；如果摘要和正文不一致，以正文中的实际命令和配置为准。
This document defines the production contract exposed by `CryptoLivePoolPipelines` to downstream strategy systems.
The current v1 artifact namespace remains `crypto-leader-rotation` for compatibility with existing published objects and downstream readers.

The upstream project publishes a monthly `core_major` live pool and exposes it through:

1. local build artifacts under `data/output/`
2. versioned and current objects in GCS
3. a lightweight Firestore summary document

## Authority Boundary

`CryptoLivePoolPipelines` owns monthly live-pool membership, ranking, and order for `crypto_leader_rotation`. Downstream strategy and execution repositories should treat the ordered `symbols` list from `live_pool.json` or `artifact_manifest.json` as canonical.

Downstream systems may apply runtime execution gates, sell rules, top-N selection, sizing, and degraded-source policy after artifact validation. They should not recalculate monthly membership or replace the published order from local indicators. `latest_ranking.csv` and `selection_meta` are upstream evidence and diagnostics; `live_pool.json` plus `artifact_manifest.json` are the stable execution contract.

## Canonical Downstream Files

### `artifact_manifest.json`

This profile-aware manifest is the canonical contract wrapper for downstream
runtimes. It lets the execution platform validate the artifact family without
binding to `CryptoLivePoolPipelines` internals.

Required stable fields:

- `manifest_type = strategy_artifact`
- `contract_version = crypto_leader_rotation.live_pool.v1`
- `strategy_profile = crypto_leader_rotation`
- `artifact_type = live_pool`
- `artifact_name = crypto_leader_rotation_live_pool`
- `as_of_date`
- `snapshot_as_of`
- `version`
- `mode`
- `symbol_count`
- `symbols`
- `source_project`
- `generated_at`
- `primary_artifact = live_pool`
- `artifacts`

The `artifacts` mapping includes relative file paths and SHA-256 checksums for:

- `latest_universe`
- `latest_ranking`
- `live_pool`
- `live_pool_legacy`

Downstream platforms should treat this manifest as the strategy artifact
contract and keep legacy `live_pool_legacy.json` parsing as a compatibility
path, not as the only contract shape.

### `live_pool_legacy.json`

This is the most convenient file for older downstream scripts that expect a direct symbol mapping.

Schema:

```json
{
  "as_of_date": "2026-03-13",
  "version": "2026-03-13-core_major",
  "mode": "core_major",
  "pool_size": 5,
  "symbols": {
    "TRXUSDT": {"base_asset": "TRX"},
    "ETHUSDT": {"base_asset": "ETH"},
    "BCHUSDT": {"base_asset": "BCH"},
    "NEARUSDT": {"base_asset": "NEAR"},
    "LTCUSDT": {"base_asset": "LTC"}
  },
  "symbol_map": {
    "TRXUSDT": {"base_asset": "TRX"},
    "ETHUSDT": {"base_asset": "ETH"},
    "BCHUSDT": {"base_asset": "BCH"},
    "NEARUSDT": {"base_asset": "NEAR"},
    "LTCUSDT": {"base_asset": "LTC"}
  },
  "source_project": "crypto-leader-rotation"
}
```

Contract notes:

- `as_of_date`: the production snapshot date in `YYYY-MM-DD`
- `version`: stable release identifier, typically `<YYYY-MM-DD>-<mode>`
- `mode`: the published production mode, currently `core_major`
- `pool_size`: number of symbols currently published
- `symbols`: mapping from `SYMBOLUSDT` to `{base_asset}`
- `symbol_map`: additive alias of the same mapping for downstreams that use the richer contract
- `source_project`: upstream publisher identity for observability and audit trails
- keys are the production `core_major` pool unless explicitly overridden during build

### `live_pool.json`

This file contains both the ordered list and the symbol mapping:

```json
{
  "as_of_date": "2026-03-13",
  "version": "2026-03-13-core_major",
  "mode": "core_major",
  "pool_size": 5,
  "symbols": ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "LTCUSDT"],
  "symbol_map": {
    "TRXUSDT": {"base_asset": "TRX"},
    "ETHUSDT": {"base_asset": "ETH"},
    "BCHUSDT": {"base_asset": "BCH"},
    "NEARUSDT": {"base_asset": "NEAR"},
    "LTCUSDT": {"base_asset": "LTC"}
  },
  "source_project": "crypto-leader-rotation"
}
```

Stable contract fields for downstream validation:

- `as_of_date`
- `version`
- `mode`
- `pool_size`
- `symbols`
- `symbol_map`
- `source_project`

Meaning:

- `pool_size` and `symbols` always describe the full official exported pool for that monthly snapshot
- `symbols` order is the official upstream monthly order and should be preserved by downstream payload normalization
- downstream display panels, local ranking previews, or final execution targets are separate downstream-layer concepts

Research/reporting extras are intentionally not part of the stable contract. Downstream consumers should not infer live readiness from local research CSVs or validation summaries.

Optional additive research extension:

- `selection_meta` may be included in local shadow-release artifacts, or in live exports if explicitly enabled
- example fields include `final_score`, `confidence`, and `current_rank`
- downstream should treat this as optional enrichment, not as a required contract field

## Firestore Contract

Collection and document defaults:

- collection: `strategy`
- document: `CRYPTO_LEADER_ROTATION_LIVE_POOL`

Payload example:

```json
{
  "as_of_date": "2026-03-13",
  "mode": "core_major",
  "version": "2026-03-13-core_major",
  "pool_size": 5,
  "symbols": ["TRXUSDT", "ETHUSDT", "BCHUSDT", "NEARUSDT", "LTCUSDT"],
  "symbol_map": {
    "TRXUSDT": {"base_asset": "TRX"},
    "ETHUSDT": {"base_asset": "ETH"},
    "BCHUSDT": {"base_asset": "BCH"},
    "NEARUSDT": {"base_asset": "NEAR"},
    "LTCUSDT": {"base_asset": "LTC"}
  },
  "storage_prefix": "gs://example-bucket/crypto-leader-rotation/releases/2026-03-13-core_major",
  "current_prefix": "gs://example-bucket/crypto-leader-rotation/current",
  "live_pool_legacy_uri": "gs://example-bucket/crypto-leader-rotation/current/live_pool_legacy.json",
  "live_pool_uri": "gs://example-bucket/crypto-leader-rotation/current/live_pool.json",
  "artifact_manifest_uri": "gs://example-bucket/crypto-leader-rotation/current/artifact_manifest.json",
  "latest_universe_uri": "gs://example-bucket/crypto-leader-rotation/current/latest_universe.json",
  "latest_ranking_uri": "gs://example-bucket/crypto-leader-rotation/current/latest_ranking.csv",
  "versioned_live_pool_legacy_uri": "gs://example-bucket/crypto-leader-rotation/releases/2026-03-13-core_major/live_pool_legacy.json",
  "versioned_artifact_manifest_uri": "gs://example-bucket/crypto-leader-rotation/releases/2026-03-13-core_major/artifact_manifest.json",
  "artifact_contract_version": "crypto_leader_rotation.live_pool.v1",
  "generated_at": "2026-03-13T13:00:00+00:00",
  "source_project": "crypto-leader-rotation"
}
```

The Firestore document intentionally excludes the full ranking CSV. Downstream readers should only rely on the summary fields above.

Stable vs additive fields:

- stable core fields: `as_of_date`, `version`, `mode`, `pool_size`, `symbols`, `symbol_map`, `source_project`
- publish-only pointer fields: `storage_prefix`, `current_prefix`, `live_pool_uri`, `live_pool_legacy_uri`, `artifact_manifest_uri`, `latest_universe_uri`, `latest_ranking_uri`, `versioned_live_pool_legacy_uri`, `versioned_artifact_manifest_uri`
- additive observability field: `generated_at`
- artifact wrapper field: `artifact_contract_version`

## GCS Path Layout

Versioned release objects:

```text
gs://<bucket>/crypto-leader-rotation/releases/<YYYY-MM-DD-mode>/latest_universe.json
gs://<bucket>/crypto-leader-rotation/releases/<YYYY-MM-DD-mode>/latest_ranking.csv
gs://<bucket>/crypto-leader-rotation/releases/<YYYY-MM-DD-mode>/live_pool.json
gs://<bucket>/crypto-leader-rotation/releases/<YYYY-MM-DD-mode>/live_pool_legacy.json
gs://<bucket>/crypto-leader-rotation/releases/<YYYY-MM-DD-mode>/artifact_manifest.json
```

Current pointers:

```text
gs://<bucket>/crypto-leader-rotation/current/latest_universe.json
gs://<bucket>/crypto-leader-rotation/current/latest_ranking.csv
gs://<bucket>/crypto-leader-rotation/current/live_pool.json
gs://<bucket>/crypto-leader-rotation/current/live_pool_legacy.json
gs://<bucket>/crypto-leader-rotation/current/artifact_manifest.json
```

## Local Shadow Release History

For end-to-end offline replay, the repository can also build a local monthly shadow release history under:

```text
data/output/shadow_releases/
  release_index.csv
  <YYYY-MM-DD-mode>/
    live_pool.json
    live_pool_legacy.json
    release_manifest.json
```

`release_index.csv` is intended for downstream local replay and includes:

- `as_of_date`
- `activation_date`
- `version`
- `mode`
- `pool_size`
- `symbols`
- optional research diagnostics such as `regime` and `regime_confidence`
- relative paths to the local artifact files

This shadow history is additive research infrastructure. It is meant to mimic the monthly upstream artifact sequence without requiring live Firestore or GCS.

## Shadow Candidate Tracks

For dual-track shadow monitoring, the repo can also build:

```text
data/output/shadow_candidate_tracks/
  track_summary.csv
  official_baseline/
    release_index.csv
    <YYYY-MM-DD-mode>/
      live_pool.json
      live_pool_legacy.json
      release_manifest.json
  challenger_topk_60/
    release_index.csv
    <YYYY-MM-DD-mode>/
      live_pool.json
      live_pool_legacy.json
      release_manifest.json
```

Track metadata that downstream may rely on for shadow comparison:

- `profile`
- `source_track`
- `candidate_status`
- `version`
- `as_of_date`
- `activation_date`
- `pool_size`
- `expected_pool_size`

Baseline remains the official production reference. `challenger_topk_60` is shadow-only in this workflow.

Monthly operator entrypoint:

```bash
.venv/bin/python scripts/run_monthly_shadow_build.py
```

This wrapper refreshes the official baseline artifacts, runs the publish dry-run manifest check, and rebuilds the dual-track shadow candidate histories without changing the production default publish target.

## Recommended Downstream Read Priority

1. Read Firestore `strategy/CRYPTO_LEADER_ROTATION_LIVE_POOL`
2. If the latest Firestore payload is invalid or unavailable, prefer the downstream script's last known good upstream payload
3. If explicitly configured, read the synchronized `live_pool_legacy.json` or `live_pool.json`
4. If all upstream-aware layers fail, fall back to the downstream script's static universe as an emergency-only path

Freshness semantics:

- `as_of_date` is the monthly production snapshot date
- downstream should validate it against its own staleness threshold
- stale upstream data is a degraded state, not the same as a healthy fresh publish
- static fallback is a last resort and should be logged as degraded

## Downstream Pseudocode

```python
def load_trend_pool():
    payload = try_read_firestore("strategy", "CRYPTO_LEADER_ROTATION_LIVE_POOL")
    if is_valid_and_fresh(payload):
        return payload["symbol_map"], {"source": "fresh_upstream"}

    cached = read_last_known_good_upstream_payload()
    if cached:
        return cached["symbol_map"], {"source": "last_known_good", "degraded": True}

    legacy = try_read_local_json("live_pool_legacy.json")
    if is_valid_local_fallback(legacy):
        return legacy["symbol_map"], {"source": "local_file", "degraded": True}

    return STATIC_TREND_UNIVERSE, {"source": "static", "degraded": True}
```

## Rollback Strategy

Preferred rollback:

1. choose the previous version under `gs://<bucket>/crypto-leader-rotation/releases/`
2. copy its four artifacts back onto the `current/` prefix
3. update the Firestore summary document so `version`, `as_of_date`, and URIs point to that release

The downstream consumer contract does not need to change during rollback.
