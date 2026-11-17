"""Verify the monthly reviewers against a fixed synthetic fixture."""
import os
from urllib.error import HTTPError

from scripts.run_monthly_dual_api_review import APIReviewer, Config, MODELS, SECRET_NAMES

MATERIAL = "## Synthetic fixture\nTwo source records were expected. Only one synthetic record was supplied."


def main():
    config = Config({"MONTHLY_REVIEW_MAX_INPUT_TOKENS": "2048", "MONTHLY_REVIEW_MAX_OUTPUT_TOKENS": "512"})
    ok = True
    for provider, secret in zip(("OpenAI", "Anthropic"), SECRET_NAMES):
        try:
            key = os.environ[secret]
            if not key:
                raise KeyError(secret)
            APIReviewer(provider, key).review(MATERIAL, config, verify_model=True)
        except HTTPError as exc:
            print(f"{provider}: HTTP {exc.code}")
            ok = False
        except Exception:
            print(f"{provider}: credential, transport or receipt check failed")
            ok = False
        else:
            print(f"{provider}: completed, provider confirmed {MODELS[provider]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
