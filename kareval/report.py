"""Markdown scorecard rendering."""

from __future__ import annotations

from .metrics import Analysis


def _humanize_seconds(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 3600:
        return f"{value / 3600:.1f}h"
    return f"{value / 60:.0f}m"


def render_markdown(analysis: Analysis) -> str:
    record = analysis.record
    metrics = analysis.metrics
    lines: list[str] = []
    lines.append(f"## Simulation scorecard — {record.case_id}")
    lines.append("")
    lines.append(
        f"- Lane: `{record.lane}`  ·  Squad: `{record.squad_version}`  ·  "
        f"Result: **{'PASS' if analysis.passed else 'FAIL'}**  ·  "
        f"Soft score: **{analysis.score}**"
    )
    lines.append(
        f"- Gate cards: {metrics['gate_cards']}  ·  "
        f"Human touches: {metrics['human_touches']} (budget {metrics['human_touch_budget']})"
    )
    wait = metrics["wait_human"]
    lines.append(
        "- Human wait: "
        f"p50 {_humanize_seconds(wait['p50_seconds'])}, "
        f"max {_humanize_seconds(wait['max_seconds'])}, "
        f"unresolved {wait['unresolved']}"
    )
    lines.append(
        f"- Rework: {metrics['rework_rounds']}  ·  "
        f"Candidate recuts: {metrics['candidate_recuts']}  ·  "
        f"Cycle time: {_humanize_seconds(metrics['cycle_time_seconds'])}"
    )
    defects = metrics["escaped_defects"]
    lines.append(
        "- Escaped defects: "
        + (", ".join(f"{k.upper()}×{v}" for k, v in sorted(defects.items())) or "none")
    )
    lines.append("")

    if analysis.violations:
        lines.append("### Hard violations")
        lines.append("")
        lines.append("| Rule | Event | Detail |")
        lines.append("| --- | --- | --- |")
        for violation in analysis.violations:
            event = "#" + str(violation.event_index) if violation.event_index is not None else "—"
            detail = violation.detail.replace("|", "\\|")
            lines.append(f"| `{violation.rule}` | {event} | {detail} |")
    else:
        lines.append("No hard-gate violations.")
    lines.append("")
    return "\n".join(lines)
