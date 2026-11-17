"""Verify the monthly reviewers against a fixed synthetic fixture."""
import os
from urllib.error import HTTPError

from scripts.run_monthly_dual_api_review import APIReviewer, Config, PROVIDERS, ReviewUnavailable, SECRET_NAMES

MATERIAL = "## Synthetic fixture\nTwo source records were expected. Only one synthetic record was supplied."


def main():
    config = Config({"MONTHLY_REVIEW_MAX_INPUT_TOKENS": "2048", "MONTHLY_REVIEW_MAX_OUTPUT_TOKENS": "512"})
    selected = os.environ.get("PROBE_PROVIDER") or "both"
    if selected not in ("both", *PROVIDERS):
        raise SystemExit("invalid probe provider")
    ok = True
    for provider, secret in zip(PROVIDERS, SECRET_NAMES):
        if selected not in ("both", provider):
            continue
        try:
            key = os.environ[secret]
            if not key:
                raise KeyError(secret)
            reviewer = APIReviewer(provider, key)
            actual, text = reviewer.review(MATERIAL, config,
                system="Return only the number of synthetic records supplied, as a single Arabic numeral.")
            if text.strip() != "1":
                raise ReviewUnavailable("incorrect synthetic record count")
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
            print(f"{provider}: completed, provider confirmed {actual}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
