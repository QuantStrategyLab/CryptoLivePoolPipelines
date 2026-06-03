# CryptoSnapshotPipelines

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

CryptoSnapshotPipelines is the QuantStrategyLab crypto snapshot and release pipeline. It builds the crypto live pool, rankings, shadow candidate tracks, and release artifacts used by CryptoStrategies.

It is an evidence-producing repository. It does not place trades and should not be treated as an execution platform.

## Strategy and evidence boundary

### Direct runtime strategies

The trading logic lives in CryptoStrategies. This repository produces the live-pool and validation artifacts that the strategy package reads.

### Snapshot-backed work handled here

- core_major live pool artifacts
- monthly live-pool shadow validation
- external-data and candidate-track research outputs

### Downstream use

CryptoStrategies and BinancePlatform should consume only release artifacts that pass the documented contract checks.

## What the artifacts are for

Snapshot artifacts are used to make strategy decisions reproducible: ranking inputs, feature snapshots, manifests, validation summaries, and promotion evidence. They are not marketing claims. Before a downstream repository promotes a profile, review the latest artifacts across short, medium, and long windows where applicable.

## Repository layout

- `src/`: library and runtime code.
- `tests/`: unit, contract, and regression tests.
- `docs/`: runbooks, design notes, evidence, and integration contracts.
- `.github/workflows/`: CI, scheduled jobs, release, or deployment workflows.
- `scripts/`: operator scripts and local helpers.
- `config/`: runtime or pipeline configuration.

## Quick start

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

## Useful docs

- [`docs/external_data_roadmap.md`](docs/external_data_roadmap.md)
- [`docs/external_data_validation.md`](docs/external_data_validation.md)
- [`docs/integration_contract.md`](docs/integration_contract.md)
- [`docs/operator_runbook.md`](docs/operator_runbook.md)
- [`docs/validation_status.md`](docs/validation_status.md)

## Safety and contribution notes

- Keep generated data, credentials, and private account details out of Git unless the artifact is intentionally public and documented.
- Prefer reproducible commands and explicit output directories.
- Do not promote a research artifact to live use without the documented validation evidence.

## License

See [LICENSE](LICENSE).
