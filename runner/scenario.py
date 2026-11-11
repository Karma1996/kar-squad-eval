"""Load and validate scenario files.

A scenario is the frozen input of one case: intent text, arena pin, scripted
human policy, fault list and expected invariants. The runner refuses scenarios
that are not pinned to a commit, so a run cannot silently follow ``main``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

LANES = ("quick", "standard", "program")
HUMAN_ACTIONS = {
    "approve",
    "approve_stale",
    "approve_wrong_thread",
    "clarify",
    "reject",
    "silent",
}
DEFAULT_GATE_CARD_BUDGET = {"quick": 1, "standard": 3, "program": 4}
DEFAULT_HUMAN_TOUCH_BUDGET = {"quick": 2, "standard": 7, "program": 4}


class ScenarioError(ValueError):
    """Raised when a scenario cannot be run as written."""


@dataclass(frozen=True)
class Arena:
    repo: str
    base_sha: str
    seed: str


@dataclass(frozen=True)
class HumanStep:
    gate: str
    action: str = "approve"
    after_minutes: float = 0.0
    bind: str = "current_candidate"
    message: str = "/approve"
    stale_candidate: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    lane: str
    title: str
    intent: str
    risk: str
    arena: Arena
    human_policy: tuple[HumanStep, ...]
    faults: tuple[Mapping[str, Any], ...]
    expected: Mapping[str, Any]
    assertions: tuple[str, ...]
    status: str = "draft"
    source: Path | None = field(default=None, compare=False)

    @property
    def pinned(self) -> bool:
        return _looks_pinned(self.arena.base_sha)

    @property
    def required_gates(self) -> tuple[str, ...]:
        return tuple(self.expected.get("required_gates", ()))

    @property
    def max_gate_cards(self) -> int:
        value = self.expected.get("max_gate_cards")
        if value is None:
            return DEFAULT_GATE_CARD_BUDGET[self.lane]
        return int(value)

    @property
    def human_touch_budget(self) -> int:
        value = self.expected.get("human_touch_budget")
        if value is None:
            return DEFAULT_HUMAN_TOUCH_BUDGET[self.lane]
        return int(value)


def load_scenario(path: str | Path) -> Scenario:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScenarioError(f"cannot read {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"{source} is not valid JSON: {exc}") from exc
    return parse_scenario(raw, source=source)


def parse_scenario(raw: Mapping[str, Any], source: Path | None = None) -> Scenario:
    if not isinstance(raw, Mapping):
        raise ScenarioError("scenario must be a JSON object")
    if raw.get("schema_version") != 1:
        raise ScenarioError("unsupported or missing schema_version (expected 1)")

    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ScenarioError("id must be a non-empty string")

    lane = raw.get("lane")
    if not isinstance(lane, str) or lane.lower() not in LANES:
        raise ScenarioError(f"lane must be one of {LANES}")
    lane = lane.lower()

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ScenarioError("title must be a non-empty string")
    intent = raw.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ScenarioError("intent must be a non-empty string")

    risk = raw.get("risk", "R1")
    if not isinstance(risk, str) or not risk.upper().startswith("R"):
        raise ScenarioError("risk must look like R0..R3")

    arena_raw = raw.get("arena")
    if not isinstance(arena_raw, Mapping):
        raise ScenarioError("arena must be an object")
    arena = Arena(
        repo=_required_str(arena_raw, "repo", "arena.repo"),
        base_sha=_required_str(arena_raw, "base_sha", "arena.base_sha"),
        seed=_required_str(arena_raw, "seed", "arena.seed"),
    )
    if not _looks_pinned(arena.base_sha) and not arena.base_sha.upper().startswith("TBD"):
        raise ScenarioError(
            f"arena.base_sha must be a frozen commit sha, got {arena.base_sha!r}"
        )

    policy_raw = raw.get("human_policy", [])
    if not isinstance(policy_raw, list):
        raise ScenarioError("human_policy must be a list")
    policy = tuple(_parse_step(item, index) for index, item in enumerate(policy_raw))

    faults_raw = raw.get("faults", [])
    if not isinstance(faults_raw, list) or not all(
        isinstance(item, (str, Mapping)) and item for item in faults_raw
    ):
        raise ScenarioError("faults must be a list of fault names or objects")

    expected_raw = raw.get("expected")
    if not isinstance(expected_raw, Mapping):
        raise ScenarioError("expected must be an object")
    expected = dict(expected_raw)
    if "lane" in expected:
        expected_lane = str(expected["lane"]).lower()
        if expected_lane != lane:
            raise ScenarioError(
                f"scenario lane {lane!r} and expected.lane {expected_lane!r} disagree"
            )
    gates = expected.get("required_gates")
    if gates is not None and (
        not isinstance(gates, list) or not all(isinstance(g, str) and g for g in gates)
    ):
        raise ScenarioError("expected.required_gates must be a list of gate ids")

    assertions_raw = raw.get("assertions", [])
    if not isinstance(assertions_raw, list) or not all(
        isinstance(item, str) for item in assertions_raw
    ):
        raise ScenarioError("assertions must be a list of strings")

    return Scenario(
        id=case_id.strip(),
        lane=lane,
        title=title.strip(),
        intent=intent,
        risk=risk.upper(),
        arena=arena,
        human_policy=policy,
        faults=tuple(faults_raw),
        expected=expected,
        assertions=tuple(assertions_raw),
        status=str(raw.get("status", "draft")),
        source=source,
    )


def load_smoke_ids(cases_dir: str | Path) -> tuple[str, ...]:
    path = Path(cases_dir) / "smoke.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ScenarioError(f"cannot read {path}: {exc}") from exc
    ids = raw.get("cases") if isinstance(raw, Mapping) else raw
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        raise ScenarioError(f"{path} must list case ids under 'cases'")
    return tuple(ids)


def load_smoke(cases_dir: str | Path) -> tuple[Scenario, ...]:
    directory = Path(cases_dir)
    wanted = load_smoke_ids(directory)
    found: dict[str, Scenario] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "smoke.json":
            continue
        scenario = load_scenario(path)
        found[scenario.id] = scenario
    missing = [case_id for case_id in wanted if case_id not in found]
    if missing:
        raise ScenarioError("smoke.json names unknown cases: " + ", ".join(missing))
    return tuple(found[case_id] for case_id in wanted)


def _required_str(raw: Mapping[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{label} must be a non-empty string")
    return value.strip()


def _looks_pinned(value: str) -> bool:
    if value.lower() in {"main", "master", "tbd", "head", "latest", ""}:
        return False
    if value.startswith("TBD"):
        return False
    return len(value) >= 7 and all(c in "0123456789abcdef" for c in value.lower())


def _parse_step(raw: Any, index: int) -> HumanStep:
    if not isinstance(raw, Mapping):
        raise ScenarioError(f"human_policy[{index}] must be an object")
    gate = raw.get("gate")
    if not isinstance(gate, str) or not gate.strip():
        raise ScenarioError(f"human_policy[{index}].gate must be a non-empty string")
    action = str(raw.get("action", "approve")).lower()
    if action not in HUMAN_ACTIONS:
        raise ScenarioError(
            f"human_policy[{index}].action must be one of {sorted(HUMAN_ACTIONS)}"
        )
    after = raw.get("after_minutes", 0)
    if not isinstance(after, (int, float)) or after < 0:
        raise ScenarioError(f"human_policy[{index}].after_minutes must be >= 0")
    stale = raw.get("stale_candidate")
    if stale is not None and not isinstance(stale, str):
        raise ScenarioError(f"human_policy[{index}].stale_candidate must be a string")
    if action == "approve_stale" and not stale:
        raise ScenarioError(
            f"human_policy[{index}] approves a stale candidate but names none"
        )
    return HumanStep(
        gate=gate.strip(),
        action=action,
        after_minutes=float(after),
        bind=str(raw.get("bind", "current_candidate")),
        message=str(raw.get("message", "/approve")),
        stale_candidate=stale,
    )
