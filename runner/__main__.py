"""Command line entry point.

Examples::

    python3 -m runner run --case cases/quick-01-hotfix.json \\
        --dry-run --script dryrun/quick-01.json --out records/quick-01.json
    python3 -m runner smoke --dry-run --out-dir records/
    python3 -m runner report --records records/ --baseline baselines/v4.4.json

Live runs refuse to start without an explicit project and assignee, because a
mis-pointed run would create real issues in a real project.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kareval import analyze
from kareval.model import parse_record

from .clock import FakeClock, RealClock
from .loop import RunConfig, RunnerError, run_case
from .multica_cli import CliMulticaPlatform
from .observers import GitHubObserver
from .platform import FakePlatform
from .report import aggregate, load_baseline, load_records_dir, render_markdown
from .scenario import ScenarioError, load_scenario, load_smoke


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runner", description="KAR squad simulation runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one scenario")
    run.add_argument("--case", required=True)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--script", help="dry-run script (required with --dry-run)")
    run.add_argument("--out", help="where to write record.json")
    run.add_argument("--project", help="sandbox project id (live)")
    run.add_argument("--assignee", help="squad / lead agent id (live)")
    run.add_argument("--poll-seconds", type=float, default=60.0)
    run.add_argument("--max-minutes", type=float, default=240.0)
    run.add_argument("--squad-version", default="4.4.0")
    run.add_argument("--human-author", default="KAR-SIM-Human")
    run.add_argument("--arena-repo", help="owner/name override for the observer")
    run.add_argument("--no-observe", action="store_true")

    smoke = sub.add_parser("smoke", help="run the frozen smoke set")
    smoke.add_argument("--cases", default="cases")
    smoke.add_argument("--dry-run", action="store_true")
    smoke.add_argument("--out-dir", default="records")
    smoke.add_argument("--script-dir", default="dryrun")
    smoke.add_argument("--max-minutes", type=float, default=240.0)

    score = sub.add_parser("score", help="score one record")
    score.add_argument("--record", required=True)
    score.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="aggregate records into a scorecard")
    report.add_argument("--records", default="records")
    report.add_argument("--baseline")
    report.add_argument("--squad-version", default="")
    report.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return _run(args)
        if args.command == "smoke":
            return _smoke(args)
        if args.command == "score":
            return _score(args)
        return _report(args)
    except (ScenarioError, RunnerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.case)
    script = None
    if args.dry_run:
        if not args.script:
            raise RunnerError("--dry-run needs --script")
        script = json.loads(Path(args.script).read_text(encoding="utf-8"))
        if isinstance(script, dict):
            script = script.get("steps", [])
        clock = FakeClock()
        platform = FakePlatform(clock)
        mode = "dry-run"
    else:
        if not args.project:
            raise RunnerError("live run needs --project (sandbox project id)")
        clock = RealClock()
        platform = CliMulticaPlatform()
        mode = "live"

    observer = None
    if not args.no_observe and not args.dry_run:
        repo = args.arena_repo or scenario.arena.repo
        if repo:
            observer = GitHubObserver(repo)

    result = run_case(
        scenario,
        platform,
        clock,
        RunConfig(
            project_id=args.project,
            assignee_id=args.assignee,
            poll_seconds=args.poll_seconds,
            max_minutes=args.max_minutes,
            squad_version=args.squad_version,
            mode=mode,
            human_author=args.human_author,
            observer=observer,
        ),
        script=script,
        out=args.out,
    )
    analysis = analyze(parse_record(result.record))
    print(
        f"{scenario.id}: {'PASS' if analysis.passed else 'FAIL'} "
        f"score={analysis.score} stop={result.stop_reason}"
    )
    if analysis.violations:
        for violation in analysis.violations:
            print(f"- {violation.rule}: {violation.detail}")
    return 0 if analysis.passed else 1


def _smoke(args: argparse.Namespace) -> int:
    scenarios = load_smoke(args.cases)
    out_dir = Path(args.out_dir)
    failures = 0
    for scenario in scenarios:
        if not args.dry_run:
            raise RunnerError("smoke currently supports --dry-run only")
        script_path = Path(args.script_dir) / f"{scenario.id}.json"
        if not script_path.exists():
            raise RunnerError(f"no dry-run script for {scenario.id}: {script_path}")
        script = json.loads(script_path.read_text(encoding="utf-8"))
        if isinstance(script, dict):
            script = script.get("steps", [])
        clock = FakeClock()
        platform = FakePlatform(clock)
        result = run_case(
            scenario,
            platform,
            clock,
            RunConfig(
                max_minutes=args.max_minutes,
                mode="dry-run",
            ),
            script=script,
            out=out_dir / f"{scenario.id}.json",
        )
        analysis = analyze(parse_record(result.record))
        failures += 0 if analysis.passed else 1
        print(
            f"{scenario.id}: {'PASS' if analysis.passed else 'FAIL'} "
            f"score={analysis.score} stop={result.stop_reason}"
        )
    return 0 if failures == 0 else 1


def _score(args: argparse.Namespace) -> int:
    from kareval import load_record, render_markdown

    analysis = analyze(load_record(args.record))
    if args.json:
        print(
            json.dumps(
                {
                    "case_id": analysis.record.case_id,
                    "passed": analysis.passed,
                    "score": analysis.score,
                    "metrics": analysis.metrics,
                    "violations": [v.as_dict() for v in analysis.violations],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_markdown(analysis))
    return 0 if analysis.passed else 1


def _report(args: argparse.Namespace) -> int:
    analyses = load_records_dir(args.records)
    report = aggregate(analyses, load_baseline(args.baseline))
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report, args.squad_version))
    return 0 if report.release_gate else 1


if __name__ == "__main__":
    raise SystemExit(main())
