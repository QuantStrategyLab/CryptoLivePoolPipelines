"""Verify the monthly reviewers against a fixed synthetic fixture."""
import os
from urllib.error import HTTPError

from scripts.run_monthly_dual_api_review import APIReviewer, Config, MODELS, ReviewUnavailable, SECRET_NAMES

MATERIAL = "## Synthetic fixture\nTwo source records were expected. Only one synthetic record was supplied."


def main():
    config = Config({"MONTHLY_REVIEW_MAX_INPUT_TOKENS": "2048", "MONTHLY_REVIEW_MAX_OUTPUT_TOKENS": "512"})
    selected = os.environ.get("PROBE_PROVIDER") or "both"
    if selected not in ("both", *MODELS):
        raise SystemExit("invalid probe provider")
    ok = True
    for provider, secret in zip(("OpenAI", "Anthropic"), SECRET_NAMES):
        if selected not in ("both", provider):
            continue
        try:
            key = os.environ[secret]
            if not key:
                raise KeyError(secret)
            reviewer = APIReviewer(provider, key)
            if provider == "Anthropic":
                reviewer.review(MATERIAL, config, verify_model=True,
                                system="Reply with only the observed number of synthetic records.")
            else:
                reviewer.review(MATERIAL, config, verify_model=True)
        except HTTPError as exc:
            print(f"{provider}: HTTP {exc.code}")
            ok = False
        except ReviewUnavailable as exc:
            print(f"{provider}: {exc}")
            ok = False
        except KeyError:
            print(f"{provider}: missing secret or required response field")
            ok = False
        except Exception:
            print(f"{provider}: credential, transport or receipt check failed")
            ok = False
        else:
            print(f"{provider}: completed, provider confirmed {MODELS[provider]}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
