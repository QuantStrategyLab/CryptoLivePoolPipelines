# Integration Contract

This document defines the production contract exposed by `CryptoLivePoolPipelines` to downstream strategy systems.
The current v1 artifact namespace is `crypto-live-pool-pipelines`.

The upstream project publishes a monthly `core_major` live pool and exposes it through:

1. local build artifacts under `data/output/`
2. versioned and current objects in GCS
3. a lightweight Firestore summary document

## Authority Boundary

`CryptoLivePoolPipelines` owns monthly live-pool membership, ranking, and order for `crypto_live_pool_rotation`. Downstream strategy and execution repositories should treat the ordered `symbols` list from `live_pool.json` or `artifact_manifest.json` as canonical.

Downstream systems may apply runtime execution gates, sell rules, top-N selection, sizing, and degraded-source policy after artifact validation. They should not recalculate monthly membership or replace the published order from local indicators. `latest_ranking.csv` and `selection_meta` are upstream evidence and diagnostics; `live_pool.json` plus `artifact_manifest.json` are the stable execution contract.

## Canonical Downstream Files

### `artifact_manifest.json`

This profile-aware manifest is the canonical contract wrapper for downstream
runtimes. It lets the execution platform validate the artifact family without
binding to `CryptoLivePoolPipelines` internals.

Required stable fields:

- `manifest_type = strategy_artifact`
- `contract_version = crypto_live_pool_rotation.live_pool.v1`
- `strategy_profile = crypto_live_pool_rotation`
- `artifact_type = live_pool`
- `artifact_name = crypto_live_pool_rotation_live_pool`
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
- `model_run_manifest` and `runtime_evidence_identity.model_run_manifest`

`model_run_manifest.json` is required for candidate and production releases. Its canonical digest is bound by `runtime_evidence_identity`; it records the actual backend, model family, model configuration, training-data digest, and source revision. A missing or mismatched binding is rejected.

The `artifacts` mapping includes relative file paths and SHA-256 checksums for:

- `latest_universe`
- `latest_ranking`
- `live_pool`
- `live_pool_legacy`

Downstream platforms should treat this manifest as the strategy artifact
contract. `live_pool_legacy.json` remains a secondary payload shape for scripts
that need a direct symbol mapping.

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
  "source_project": "crypto-live-pool-pipelines"
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
  "source_project": "crypto-live-pool-pipelines"
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
- document: `CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL`

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
  "storage_prefix": "gs://example-bucket/crypto-live-pool-pipelines/releases/2026-03-13-core_major",
  "current_prefix": "gs://example-bucket/crypto-live-pool-pipelines/current",
  "live_pool_legacy_uri": "gs://example-bucket/crypto-live-pool-pipelines/current/live_pool_legacy.json",
  "live_pool_uri": "gs://example-bucket/crypto-live-pool-pipelines/current/live_pool.json",
  "artifact_manifest_uri": "gs://example-bucket/crypto-live-pool-pipelines/current/artifact_manifest.json",
  "latest_universe_uri": "gs://example-bucket/crypto-live-pool-pipelines/current/latest_universe.json",
  "latest_ranking_uri": "gs://example-bucket/crypto-live-pool-pipelines/current/latest_ranking.csv",
  "versioned_live_pool_legacy_uri": "gs://example-bucket/crypto-live-pool-pipelines/releases/2026-03-13-core_major/live_pool_legacy.json",
  "versioned_artifact_manifest_uri": "gs://example-bucket/crypto-live-pool-pipelines/releases/2026-03-13-core_major/artifact_manifest.json",
  "artifact_contract_version": "crypto_live_pool_rotation.live_pool.v1",
  "generated_at": "2026-03-13T13:00:00+00:00",
  "source_project": "crypto-live-pool-pipelines"
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
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/latest_universe.json
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/latest_ranking.csv
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/live_pool.json
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/live_pool_legacy.json
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/model_run_manifest.json
gs://<bucket>/crypto-live-pool-pipelines/releases/<YYYY-MM-DD-mode>/artifact_manifest.json
```

Current pointers:

```text
gs://<bucket>/crypto-live-pool-pipelines/current/latest_universe.json
gs://<bucket>/crypto-live-pool-pipelines/current/latest_ranking.csv
gs://<bucket>/crypto-live-pool-pipelines/current/live_pool.json
gs://<bucket>/crypto-live-pool-pipelines/current/live_pool_legacy.json
gs://<bucket>/crypto-live-pool-pipelines/current/model_run_manifest.json
gs://<bucket>/crypto-live-pool-pipelines/current/artifact_manifest.json
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

1. Read Firestore `strategy/CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL`
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
    payload = try_read_firestore("strategy", "CRYPTO_LIVE_POOL_ROTATION_LIVE_POOL")
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

1. choose the previous version under `gs://<bucket>/crypto-live-pool-pipelines/releases/`
2. copy its four artifacts back onto the `current/` prefix
3. update the Firestore summary document so `version`, `as_of_date`, and URIs point to that release

The downstream consumer contract does not need to change during rollback.

## BTC cycle indicator boundaries

On 2026-10-30, the pool release contract was clarified for consumers: the
optional cycle file is not published by the pool release publisher and must not
be inferred from the pool's other published artifacts.

The optional `btc_cycle_indicators.json` is calculated from the named `close`
column of local BTCUSDT history at or before the requested date. It is not a
`btc-cycle-input/v1` envelope and does not attest a source, producer, actual
observation time, or availability time. CSV dates alone do not establish that
the selected Binance bar was closed or that the history is point-in-time
complete. Older history files may retain an unclosed last day. Do not infer a
real availability time from a date.

On 2026-10-30, daily history collection was changed to refetch its last stored
day, retain exchange-provided `open_time` and `close_time` in milliseconds, and
store `received_at` as the local UTC clock time immediately after each response.
Rows from the current response are saved only when the declared daily close
precedes that receipt time. The local clock and provider response are not
independently authenticated. An empty or failed refresh retains existing rows;
an older unverified last row can therefore remain, with a warning when observed.
Existing CSVs remain readable, and older rows without these columns are not
backfilled with invented evidence. Each response page must stay within the
requested time bounds and be strictly increasing across pages; invalid pages
abort the update before the history file is written.

The BTC export reader accepts optional `available_by` (or the build function's
`btc_available_by`) as a separate explicit UTC cutoff. When supplied it rejects
selected rows with missing days, invalid exchange boundary timestamps or without
a locally recorded receipt by that cutoff. This BTC-only continuity test does
not establish source authenticity; the general downloader does not impose it
on other assets with legitimately sparse histories. `as_of_date` still chooses historical bar dates; a
later receipt of an old bar cannot prove it was available at the earlier date.
Refetching a bar replaces its local row and receipt time, so earlier receipt
evidence cannot be replayed from this mutable CSV alone.
This check concerns the selected local file only and does not prove source
authenticity, clock accuracy, completeness, historical revisions or full PIT.
The existing build call does not pass this optional cutoff; its cycle output
therefore remains unverified and is not a strict authenticated input. The six
standard pool artifacts and their publication contract are unchanged.
At the start of a build, any existing `btc_cycle_indicators.json` is moved
unchanged to a unique directory under `btc-cycle-history/`. A new result is
written in a temporary directory and moved into the current path only after the
export succeeds. If the BTC file is missing or export fails, the build returns
an empty cycle result and leaves the current path absent. An earlier build
failure also leaves it absent once retirement has succeeded. Archive names are
not timestamps or evidence of historical availability; this preserves old
bytes but does not reconstruct historical PIT. Consumers of this local file
must treat an absent current path or an empty returned result as unavailable.
Builds sharing an output directory must be serialized, and readers must wait
for the intended build to finish. Retirement or archive failures abort the
build; callers must not consume an old path after an unsuccessful retirement.

The exported field `mvrv_zscore` is a price-derived proxy: it standardizes the
Mayer Multiple against its rolling mean and standard deviation. It is not an
on-chain MVRV Z-Score. Its suitability for any strategy or exit policy requires
separate approval; this label does not establish equivalence.

The cycle artifact is optional and local to the build. A failed or skipped
generation can leave a file from an earlier build in a reused output directory.
Use an isolated output directory for each build. The pool release publisher
does not publish this file: its identity, closed-bar evidence, availability,
and binding to a specific build remain unresolved. Adding publication requires
a separately reviewed contract. This does not remove or invalidate any object
already present in remote storage. This source change neither establishes a
live data connection nor authorizes production trading.

### Manual BTC evidence

The manually dispatched BTC evidence workflow uses a clean, exact `main`
checkout and downloads at most one bounded response containing 220 closed
BTCUSDT daily bars from the public Binance Spot endpoint. It has no account
credentials or scheduled trigger and does not publish a pool release. The
explicit local command is `python scripts/btc_cycle_evidence.py collect
--directory <new-directory>`; without `collect`, the command runs offline
verification of an existing directory. The workflow retains the response body,
seven-field `btc-cycle-input/v1` envelope, manifest and verification summary as
a seven-day Actions artifact, not as a trading input or pool publication.
When the endpoint or HTTP status is rejected, the bounded response body and a
separate failure manifest are retained by the same workflow. The manifest
records the HTTP status, whether the fixed endpoint matched, local receipt time
and response hash without retaining response headers or an untrusted URL.
Collection still fails and produces no cycle envelope; failure evidence cannot
be replayed or consumed as a successful market input.

The envelope's `observed_at` is the last bar's exchange-declared close time;
`available_at` is the local completion time of the calculation. The manifest
separately records the response receipt and a local readback after the page and
envelope were written. A decision clock must be at or after that readback. The
available timestamp does not establish delivery to any consumer. The verifier
replays the retained response bytes through daily-bar and BTC strict checks,
requires the latest closed UTC day and 220 consecutive days, recalculates AHR,
and compares the result and source/lock/version bindings. It refuses stale,
future, malformed, modified or incomplete evidence. Its maximum age is 172,800
seconds; a consumer with a smaller configured maximum age must separately
enforce that stricter limit at its actual decision time.

The manifest is produced by the same process and its hashes establish local
consistency only: the source identity, TLS endpoint, clock, and retained bytes
are not independently attested. A response collected today does not prove that
the historical values were available at their historical dates. The workflow
does not authenticate source or clock, validate a deployment, connect an
account, or authorize trading. The separately named `mvrv_zscore` remains a
price proxy and is not part of the seven-field envelope.

