"""Aggregate per-case records into the scorecard and release gate.

The gate is intentionally conservative: a single hard violation fails the
release, and the soft score may not drop by more than two points against the
recorded baseline. Tone of voice, document layout and split decisions are not
scored here; they never should be.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from kareval import Analysis, analyze, load_record
from kareval.model import RecordError

LANES = ("quick", "standard", "program")
SOFT_DROP_LIMIT = 2.0


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    lane: str
    passed: bool
    score: float
    violations: tuple[str, ...]


@dataclass(frozen=True)
class LaneSummary:
    lane: str
    cases: int
    passed: int
    hard_violations: int
    mean_score: float
    baseline: float | None = None

    @property
    def trend(self) -> str:
        if self.baseline is None:
            return "n/a"
        delta = self.mean_score - self.baseline
        if delta > 1:
            return f"↑ {delta:+.1f}"
        if delta < -1:
            return f"↓ {delta:+.1f}"
        return f"= {delta:+.1f}"


@dataclass(frozen=True)
class AggregateReport:
    results: tuple[CaseResult, ...]
    lanes: tuple[LaneSummary, ...]
    hard_violations: int
    mean_score: float
    baseline_score: float | None
    release_gate: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cases": [
                {
                    "case_id": item.case_id,
                    "lane": item.lane,
                    "passed": item.passed,
                    "score": item.score,
                    "violations": list(item.violations),
                }
                for item in self.results
            ],
            "lanes": [
                {
                    "lane": item.lane,
                    "cases": item.cases,
                    "passed": item.passed,
                    "hard_violations": item.hard_violations,
                    "mean_score": item.mean_score,
                    "trend": item.trend,
                }
                for item in self.lanes
            ],
            "hard_violations": self.hard_violations,
            "mean_score": self.mean_score,
            "release_gate": self.release_gate,
            "reasons": list(self.reasons),
        }


def load_analyses(paths: Iterable[str | Path]) -> list[Analysis]:
    analyses: list[Analysis] = []
    for path in paths:
        analyses.append(analyze(load_record(path)))
    return analyses


def load_records_dir(directory: str | Path) -> list[Analysis]:
    root = Path(directory)
    paths = sorted(
        path
        for path in root.glob("*.json")
        if not path.name.startswith(("baseline", "."))
    )
    if not paths:
        raise RecordError(f"no record.json found under {root}")
    return load_analyses(paths)


def aggregate(
    analyses: Iterable[Analysis],
    baseline: dict[str, Any] | None = None,
) -> AggregateReport:
    analyses = list(analyses)
    if not analyses:
        raise ValueError("no analyses to aggregate")
    baseline = baseline or {}
    baseline_lanes = {
        str(key).lower(): float(value)
        for key, value in (baseline.get("lanes") or {}).items()
        if isinstance(value, (int, float))
    }
    baseline_score = baseline.get("soft")
    baseline_score = float(baseline_score) if isinstance(baseline_score, (int, float)) else None

    results = tuple(
        CaseResult(
            case_id=item.record.case_id,
            lane=item.record.lane,
            passed=item.passed,
            score=item.score,
            violations=tuple(v.rule for v in item.violations),
        )
        for item in analyses
    )
    lanes: list[LaneSummary] = []
    for lane in LANES:
        subset = [item for item in analyses if item.record.lane == lane]
        if not subset:
            continue
        lanes.append(
            LaneSummary(
                lane=lane,
                cases=len(subset),
                passed=sum(1 for item in subset if item.passed),
                hard_violations=sum(len(item.violations) for item in subset),
                mean_score=round(
                    sum(item.score for item in subset) / len(subset), 1
                ),
                baseline=baseline_lanes.get(lane),
            )
        )

    hard = sum(len(item.violations) for item in analyses)
    mean_score = round(sum(item.score for item in analyses) / len(analyses), 1)
    reasons: list[str] = []
    if hard:
        reasons.append(f"{hard} hard violation(s)")
    if any(not item.passed for item in analyses):
        reasons.append("at least one case failed")
    if baseline_score is not None and mean_score < baseline_score - SOFT_DROP_LIMIT:
        reasons.append(
            f"soft score {mean_score} dropped more than {SOFT_DROP_LIMIT:.0f} "
            f"points below baseline {baseline_score}"
        )

    return AggregateReport(
        results=results,
        lanes=tuple(lanes),
        hard_violations=hard,
        mean_score=mean_score,
        baseline_score=baseline_score,
        release_gate=not reasons,
        reasons=tuple(reasons),
    )


def baseline_payload(report: AggregateReport, squad_version: str = "") -> dict[str, Any]:
    """Serialize a *green* scorecard as the next baseline.

    Refusing a failing run is the point: a baseline recorded from a broken run
    would make the next regression gate meaningless.
    """
    if not report.release_gate:
        raise ValueError(
            "refusing to write a baseline from a failing run: "
            + "; ".join(report.reasons)
        )
    return {
        "squad_version": squad_version or "4.4.0",
        "soft": report.mean_score,
        "lanes": {lane.lane: lane.mean_score for lane in report.lanes},
        "cases": [item.case_id for item in report.results],
    }


def load_baseline(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def render_markdown(report: AggregateReport, squad_version: str = "") -> str:
    lines = [
        f"# KAR Squad Eval — {squad_version or 'unversioned'}",
        "",
        "| Lane | Cases | Pass | Hard viol. | Soft | Trend |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lane in report.lanes:
        lines.append(
            f"| {lane.lane.capitalize()} | {lane.cases} | {lane.passed}/{lane.cases} "
            f"| {lane.hard_violations} | {lane.mean_score:.1f} | {lane.trend} |"
        )
    lines += [
        "",
        f"Mean soft score: **{report.mean_score:.1f}**"
        + (
            f" (baseline {report.baseline_score:.1f})"
            if report.baseline_score is not None
            else ""
        ),
        "",
    ]
    failures = [item for item in report.results if not item.passed]
    if failures:
        lines.append("## Failures")
        lines.append("")
        for item in failures:
            lines.append(f"- `{item.case_id}` ({item.lane}): {', '.join(item.violations)}")
        lines.append("")
    lines.append(
        "Release gate: **PASS**" if report.release_gate else "Release gate: **FAIL**"
    )
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines).rstrip() + "\n"
