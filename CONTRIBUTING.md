# Contributing

Thanks for contributing to `CryptoLivePoolPipelines`.

## Ground Rules

- Prefer small, low-risk pull requests.
- Keep refactors separate from behavior changes.
- Add or update tests when changing runtime behavior.
- Do not use deployment or scheduled workflows as a substitute for local verification.
- Changes that touch live trading, credentials, permissions, Cloud Run, or exchange/broker APIs must be verified in a test environment or dry run first; do not edit production based on examples alone.

## Branching and Pull Requests

- Create a topic branch for each change.
- Open a pull request with a short summary and a concrete test plan.
- Wait for CI to pass before merging.

## Local Verification

Run the main verification command before opening a pull request:

```bash
REQ_FILE="requirements-lock.txt"; [ -f "$REQ_FILE" ] || REQ_FILE="requirements.txt"; python3 -m pip install -r "$REQ_FILE" && python3 -m unittest discover -s tests -v
```
