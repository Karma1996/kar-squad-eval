#!/usr/bin/env python3
"""Score one simulation record and print a Markdown scorecard.

Usage:
    python3 eval/score_record.py --record eval/fixtures/quick-01-pass.json
    python3 eval/score_record.py --record <path> --json --out scorecard.md

Exit codes: 0 = case passed, 1 = hard violation, 2 = bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kareval import RecordError, analyze, load_record, render_markdown  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a KAR simulation record")
    parser.add_argument("--record", required=True, help="path to record.json")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print JSON instead of Markdown (Markdown is still written with --out)",
    )
    parser.add_argument("--out", help="write the Markdown scorecard to this path")
    args = parser.parse_args(argv)

    try:
        record = load_record(args.record)
    except RecordError as exc:
        print(f"invalid record: {exc}", file=sys.stderr)
        return 2

    analysis = analyze(record)
    markdown = render_markdown(analysis)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")

    payload = {
        "case_id": record.case_id,
        "lane": record.lane,
        "squad_version": record.squad_version,
        "passed": analysis.passed,
        "score": analysis.score,
        "metrics": analysis.metrics,
        "violations": [v.as_dict() for v in analysis.violations],
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not args.out:
        print(markdown)
    else:
        print(f"wrote {args.out}")

    return 0 if analysis.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
