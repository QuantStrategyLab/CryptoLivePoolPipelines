# External Data Roadmap

## Why Binance-Only Is Not Enough

The current repository intentionally starts with Binance Spot daily OHLCV because it is easy to audit, reproducible, and sufficient for a strong practical baseline.

But Binance-only still leaves several gaps:

1. some assets were liquid elsewhere before Binance listed them
2. some earlier cycles are only partially visible in Binance history
3. Binance-only history can understate how long a coin has really been a mainstream tradable asset
4. large-cap / mainstream filtering would eventually benefit from optional market-cap context

## First-Priority External Enhancements

This roadmap deliberately keeps the first phase narrow and practical.

Priority order:

1. pre-Binance daily history merge
2. alternate exchange daily history merge
3. optional market-cap metadata

Not first priority:

- news / sentiment
- complex on-chain features
- social data
- derivatives funding or liquidation features

## Current Preparation Implemented

The repository now includes:

- [src/external_data.py](../src/external_data.py)
- [scripts/validate_external_data.py](../scripts/validate_external_data.py)
- external-data config blocks in [config/default.yaml](../config/default.yaml)

Implemented concepts:

- provider abstraction for local CSV history sources
- provider abstraction for local CSV metadata sources
- merge rules with explicit source priority
- duplicate-date resolution
- source tagging per row
- optional market-cap metadata loader

## Merge Rules

The intended default merge behavior is:

1. normalize every source into a canonical daily OHLCV schema
2. attach `data_source` and `data_provider`
3. rank sources by configured priority
4. on duplicate dates, keep the higher-priority source
5. sort the final frame by date
6. ensure the merged frame is monotonic and deduplicated

Default priority:

1. `binance`
2. `alternate_exchange`
3. `pre_binance`

Practical interpretation:

- if Binance and external overlap, Binance wins by default
- if Binance starts late, earlier external rows can extend the history backward
- if Binance has gaps and alternate exchange covers them, alternate exchange can fill those dates

## Validation Needed Before Production Enablement

Before `external_data.enabled` should be turned on in production, the following still need to be validated with real provider data:

1. symbol mapping quality across providers
2. timestamp normalization and timezone consistency
3. split / redenomination / contract migration edge cases
4. quote-volume comparability across exchanges
5. source coverage stability across major coins
6. backtest parity checks between Binance-only and merged-history runs
7. live-pool stability comparison between Binance-only and merged-history runs

## How To Compare Binance-Only vs External-Data Versions

Recommended evaluation flow:

1. run the current Binance-only baseline
2. enable external data for a controlled symbol subset
3. compare:
   - universe size changes
   - first-eligible dates
   - leader capture metrics
   - live pool turnover
   - top-N overlap with the Binance-only build
4. only expand external usage after point-in-time behavior is verified

## What Still Needs Real Providers

The code structure is ready, but real production providers are not yet wired in.

Typical next candidates:

- Kaiko / Coin Metrics / CCXT-backed daily history
- exchange archival CSV dumps
- internal curated pre-Binance history files
- optional market-cap snapshots from a stable metadata provider

The current validation script uses mock local CSV inputs only.
