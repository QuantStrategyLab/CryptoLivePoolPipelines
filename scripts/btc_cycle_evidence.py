#!/usr/bin/env python3
"""Offline verification by default; explicit collect is the only network action."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.btc_evidence import collect, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("verify", "collect"), default="verify")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--decision-at", default=None, help="Explicit UTC decision cutoff for offline verification")
    args = parser.parse_args()
    if args.mode == "collect":
        if args.decision_at:
            parser.error("--decision-at is only valid for verify")
        result = collect(args.directory)
    else:
        result = verify(args.directory, decision_at=args.decision_at)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
