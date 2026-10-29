# External Data Validation

This document records the current external-data validation state after multiple real reruns and profile sweeps.

Current top-line conclusion:

- `Binance-only` is still the formal production default.
- the best external-data variant is now much closer to production quality than earlier versions.
- but it still does **not** win clearly enough across `30 / 60 / 90` horizon quality metrics to replace `Binance-only` as the default live mode.
- the best current experimental profile is `external_data_core_only_no_doge`.

## Current External-Data Policy

The external backfill policy now has two tiers.

Core backfill whitelist:

- `BTCUSDT`
- `ETHUSDT`
- `XRPUSDT`
- `LTCUSDT`
- `BCHUSDT`
- `TRXUSDT`
- `ADAUSDT`
- `SOLUSDT`

Cautious backfill whitelist:

- `DOGEUSDT`

Current default behavior:

- core symbols are eligible for merge
- cautious symbols are tracked and reported
- cautious symbols are **not** merged by default

This is controlled by:

- `external_data.merge_cautious_symbols: false`

So the current default experimental version is effectively:

- `core_only_no_doge`

## Why DOGEUSDT Is Now A Cautious Holdout

`DOGEUSDT` still passes the raw mechanical quality checks:

- no duplicate-date issue
- monotonic time order is clean
- no large calendar gaps
- overlap consistency vs Binance is acceptable
- second-provider cross-check is acceptable

But it is now treated as a `cautious_holdout`, not an approved merge symbol.

Reason:

- `DOGE` is materially more exposed to theme rotation, social amplification, and event-driven repricing than long-cycle majors such as `ETH`, `XRP`, or `LTC`
- even when its historical series is usable, it is less representative of a stable "core major" backfill asset
- the profile sweep showed that removing DOGE from active merge materially improved the external-data variant

Current report fields for DOGE:

- `whitelist_tier = cautious`
- `caution_reason = theme_and_sentiment_sensitive_meme_major`
- `quality_status = cautious_holdout`
- `final_decision = cautious_holdout`

## Providers Used

Primary backfill provider:

- `CryptoCompare histoday`
- role: `pre_binance`
- purpose: extend history backward before Binance coverage starts

Secondary cross-check provider:

- `CryptoDataDownload` exchange-archive daily CSVs
- role: `crosscheck_history`
- purpose: cross-check the primary provider on whitelist majors only

Why this second provider is used instead of Yahoo:

- exchange-style daily CSV archives are more stable for long crypto history checks
- coverage is better for the current whitelist majors
- alignment is better with exchange OHLCV conventions
- it works better as a provider-divergence sanity check for this use case

Current exchange-archive mapping:

- Bitstamp daily CSVs for `BTC`, `ETH`, `XRP`, `LTC`, `BCH`, `DOGE`
- Bitfinex daily CSVs for `TRX`, `ADA`, `SOL`

## Quality Gate

Every external candidate is evaluated with:

- whitelist membership
- duplicate-date check
- monotonic time check
- gap count
- max gap size
- overlap-period consistency vs Binance
- missing core field check
- suspicious jump count
- minimum pre-Binance extension length
- second-provider cross-check summary

The second provider is an enhanced quality signal.

It can reject only when the anomaly is clearly severe. Normal daily-close differences are tolerated so acceptable symbols are not falsely rejected for session-boundary reasons.

## Current Quality Report

Artifact:

- `data/reports/external_data_quality_report.csv`

Current decisions:

- `approved_core`: 8 symbols
- `approved_cautious`: 0 symbols
- `cautious_holdout`: 1 symbol
- `rejected`: 0 symbols

Approved core symbols:

- `BTCUSDT`
- `ETHUSDT`
- `XRPUSDT`
- `LTCUSDT`
- `BCHUSDT`
- `TRXUSDT`
- `ADAUSDT`
- `SOLUSDT`

Cautious holdout symbols:

- `DOGEUSDT`

Rejected symbols:

- none in this round

Representative pre-Binance extension results:

| Symbol | Tier | Pre-Binance rows added | Cross-check status | Final decision | Notes |
| --- | --- | ---: | --- | --- | --- |
| BTCUSDT | core | 1826 | pass | approved_core | |
| ETHUSDT | core | 1243 | pass | approved_core | |
| XRPUSDT | core | 1433 | pass | approved_core | |
| LTCUSDT | core | 1826 | pass | approved_core | |
| BCHUSDT | core | 849 | warn | approved_core | `crosscheck_return_corr_warn` |
| TRXUSDT | core | 484 | pass | approved_core | |
| ADAUSDT | core | 457 | pass | approved_core | |
| SOLUSDT | core | 123 | pass | approved_core | |
| DOGEUSDT | cautious | 0 merged | informational | cautious_holdout | `theme_and_sentiment_sensitive_meme_major` |

Interpretation:

- the eight core symbols now have approved pre-Binance extensions
- `DOGEUSDT` remains visible in governance and reporting, but it no longer alters the active merged history set
- `BCHUSDT` stays approved, but the second provider still raises a warning rather than a reject

## Which Symbols Stay Binance-Only

Everything outside the core whitelist remains `Binance-only`.

That includes:

- `NEARUSDT`
- `HBARUSDT`
- `SUIUSDT`
- `ETCUSDT`
- `XLMUSDT`
- and the rest of the non-whitelist universe

In addition, the cautious tier currently stays `Binance-only` by default:

- `DOGEUSDT`

## Formal Comparison Setup

The comparison uses a common evaluation start date of `2020-12-21`.

Artifacts:

- `data/reports/binance_only_vs_external_data_summary.csv`
- `data/reports/external_data_quality_report.csv`
- `data/reports/external_data_symbol_coverage.csv`
- `data/reports/binance_only/`
- `data/reports/external_data/`
- `data/output/binance_only/`
- `data/output/external_data/`

## Research Comparison

`final_score` strategy:

| Metric | Binance-only | Current external-data | Delta |
| --- | ---: | ---: | ---: |
| CAGR | 0.5334 | 0.6145 | +0.0811 |
| Sharpe | 0.9465 | 1.0115 | +0.0650 |
| Max Drawdown | -0.7677 | -0.7392 | +0.0285 |
| Turnover | 16.5716 | 16.4948 | -0.0768 |

Leader metrics:

| Metric | Binance-only | Current external-data | Delta |
| --- | ---: | ---: | ---: |
| H30 Precision@N | 0.2131 | 0.2144 | +0.0012 |
| H30 Leader Capture | 0.1784 | 0.2119 | +0.0335 |
| H60 Precision@N | 0.2214 | 0.1975 | -0.0239 |
| H60 Leader Capture | 0.1774 | 0.1962 | +0.0189 |
| H90 Precision@N | 0.2179 | 0.1923 | -0.0256 |
| H90 Leader Capture | 0.1731 | 0.1577 | -0.0154 |

Interpretation:

- research CAGR improved
- research Sharpe improved
- drawdown improved
- turnover improved slightly
- H30 precision and capture improved
- H60 capture improved, but H60 precision fell
- H90 precision and H90 capture are still worse

## Walk-Forward Comparison

Mean window metrics:

| Metric | Binance-only | Current external-data | Delta |
| --- | ---: | ---: | ---: |
| H30 Precision | 0.2167 | 0.2183 | +0.0015 |
| H30 Leader Capture | 0.1843 | 0.2114 | +0.0271 |
| H60 Precision | 0.2217 | 0.2040 | -0.0178 |
| H60 Leader Capture | 0.1833 | 0.1991 | +0.0158 |
| H90 Precision | 0.2231 | 0.1977 | -0.0255 |
| H90 Leader Capture | 0.1935 | 0.1555 | -0.0380 |
| Mean Window Sharpe | 0.8810 | 0.9123 | +0.0312 |
| Mean Window Turnover | 16.9076 | 17.0003 | +0.0927 |

Interpretation:

- H30 precision improved
- H30 capture improved materially
- H60 capture improved, but H60 precision is still weaker
- H90 precision and H90 capture remain clearly worse
- mean walk-forward Sharpe improved
- turnover is now nearly flat vs Binance-only

## Live Pool Impact

Latest snapshot date: `2026-03-13`

Latest live pools:

- Binance-only:
  - `TRXUSDT`
  - `ETHUSDT`
  - `BCHUSDT`
  - `NEARUSDT`
  - `LTCUSDT`
- Current external-data:
  - `TRXUSDT`
  - `ETHUSDT`
  - `BCHUSDT`
  - `LTCUSDT`
  - `SOLUSDT`

Interpretation:

- the current external-data live pool still differs only modestly from Binance-only
- the swap is now `SOL` in and `NEAR` out, which is a cleaner major-for-major change than earlier noisy variants
- `DOGEUSDT` does not enter the current live pool and does not alter the active merged-history set

## Profile Sweep: What Worked And What Did Not

Sweep artifact:

- `data/reports/external_profile_sweep_venv/profile_summary.csv`

Tested profiles:

- `current_core_plus_doge`
- `core_only_no_doge`
- `core_no_doge_no_bch`
- `core_no_doge_no_sol`

Effective attempt:

- disabling DOGE active merge while keeping it as a cautious holdout
- this was the strongest external-data profile across research CAGR, research Sharpe, max drawdown, and mean walk-forward Sharpe

Ineffective attempts:

- removing `BCHUSDT` from core
- removing `SOLUSDT` from core

Those stricter variants reduced the gains too much and did not fix the long-horizon precision weakness.

## Recommendation

Current recommendation: **still keep Binance-only as the formal production default**.

Why:

1. the current external-data version is now materially better than earlier external-data runs
2. it improves research CAGR, research Sharpe, drawdown, and mean walk-forward Sharpe
3. it improves H30 capture and H60 capture
4. turnover is now nearly flat vs Binance-only
5. but it still underperforms on H60 precision
6. it still underperforms on H90 precision
7. it still underperforms on H90 capture

That means the current external-data branch is now a serious experimental candidate, but it is **not yet** a clear enough winner across the full `30 / 60 / 90` objective to replace the default baseline.

Production decision:

- keep `Production v1 = Binance-only + core_major + monthly publish`
- keep `external-data` in the repository as `experimental only`
- do not enable `external_data.enabled` in the default production path

## Best Current Use

Recommended current usage:

- keep `Binance-only` as the formal default production mode
- keep the current external-data branch as the best experimental candidate
- allow merges only for the approved core list
- keep `DOGEUSDT` visible as a cautious holdout, not as an active merged symbol

## Remaining Blockers Before Default Enablement

1. recover H60 precision without losing the recent H30/H60 capture gains
2. recover H90 precision and H90 capture
3. keep turnover near-flat while preserving the research / walk-forward Sharpe gains
4. continue watching `BCHUSDT` because its cross-check warning still persists
5. optionally add one more curated second-history source for the approved core majors before reconsidering default enablement

## Bottom Line

The external-data branch is now:

- tiered
- whitelist-controlled
- quality-gated
- second-provider cross-checked
- DOGE-safe by default
- and much closer to production quality than before

But the current best conclusion is still:

- `Binance-only` remains the formal default production version
- `external-data` remains the best experimental version, but not yet the default
- the current best experimental profile is `external_data_core_only_no_doge`
