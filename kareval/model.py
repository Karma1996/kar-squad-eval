"""Load and validate simulation records.

A record is the immutable, platform-derived transcript of one simulation run.
Everything the scorer needs must be in the record; the scorer never calls the
Multica API and never trusts metrics reported by the squad under test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

LANES = ("quick", "standard", "program")

#: Required fields of a gate card. The values themselves are free text; the
#: point is that a human can decide from the card alone.
GATE_CARD_FIELDS = (
    "action",
    "decision_owner",
    "version",
    "checks",
    "rationale",
    "conditions",
    "copy_line",
    "consequence",
)
GATE_CARD_MAX_LINES = 8

DEFAULT_REQUIRED_GATES = {
    "quick": ("quick-accept-merge",),
    "standard": ("h3-accept-merge",),
    "program": ("h3-accept-merge",),
}
DEFAULT_GATE_CARD_BUDGET = {"quick": 1, "standard": 3, "program": 4}
DEFAULT_HUMAN_TOUCH_BUDGET = {"quick": 2, "standard": 7, "program": 4}


class RecordError(ValueError):
    """Raised when a record cannot be scored as written."""


@dataclass(frozen=True)
class Event:
    index: int
    t: datetime
    type: str
    data: Mapping[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def actor_type(self) -> str:
        return str(self.data.get("actor_type", ""))


@dataclass(frozen=True)
class Expected:
    lane: str | None
    required_gates: tuple[str, ...]
    max_gate_cards: int
    human_touch_budget: int
    docs_required: tuple[str, ...]
    require_ci: bool = True


@dataclass(frozen=True)
class Record:
    case_id: str
    lane: str
    squad_version: str
    expected: Expected
    events: tuple[Event, ...]
    source: Path | None = None

    @property
    def first(self) -> Event:
        return self.events[0]

    @property
    def last(self) -> Event:
        return self.events[-1]

    def of_type(self, event_type: str) -> tuple[Event, ...]:
        return tuple(e for e in self.events if e.type == event_type)


def canonical_lane(value: str) -> str | None:
    """Map free-form lane text to one of the three canonical lanes."""
    text = value.strip().lower()
    for lane in LANES:
        if text == lane or text.startswith(lane):
            return lane
    return None


def _parse_time(value: Any, index: int) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RecordError(f"event {index}: missing ISO-8601 timestamp 't'")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RecordError(f"event {index}: bad timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise RecordError(f"event {index}: timestamp {value!r} needs an offset (use Z)")
    return parsed


def _parse_expected(raw: Mapping[str, Any], lane: str) -> Expected:
    lane = lane.lower()
    expected_lane = raw.get("lane")
    if expected_lane is not None:
        expected_lane = canonical_lane(str(expected_lane))
        if expected_lane is None:
            raise RecordError(f"expected.lane must be one of {LANES}")
        if expected_lane != lane:
            raise RecordError(
                f"record lane {lane!r} and expected.lane {expected_lane!r} disagree"
            )

    gates = raw.get("required_gates", list(DEFAULT_REQUIRED_GATES[lane]))
    if not isinstance(gates, (list, tuple)) or not all(
        isinstance(g, str) and g for g in gates
    ):
        raise RecordError("expected.required_gates must be a list of gate ids")

    budget = raw.get("max_gate_cards", DEFAULT_GATE_CARD_BUDGET[lane])
    if not isinstance(budget, int) or budget < 0:
        raise RecordError("expected.max_gate_cards must be a non-negative integer")

    touches = raw.get("human_touch_budget", DEFAULT_HUMAN_TOUCH_BUDGET[lane])
    if not isinstance(touches, int) or touches < 0:
        raise RecordError("expected.human_touch_budget must be a non-negative integer")

    docs = raw.get("docs_required", [])
    if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
        raise RecordError("expected.docs_required must be a list of paths")

    require_ci = raw.get("require_ci", True)
    if not isinstance(require_ci, bool):
        raise RecordError("expected.require_ci must be a boolean")

    return Expected(
        lane=expected_lane,
        required_gates=tuple(gates),
        max_gate_cards=budget,
        human_touch_budget=touches,
        docs_required=tuple(docs),
        require_ci=require_ci,
    )


def load_record(path: str | Path) -> Record:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecordError(f"cannot read {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RecordError(f"{source} is not valid JSON: {exc}") from exc
    return parse_record(raw, source=source)


def parse_record(raw: Mapping[str, Any], source: Path | None = None) -> Record:
    if not isinstance(raw, Mapping):
        raise RecordError("record must be a JSON object")
    if raw.get("schema_version") != 1:
        raise RecordError("unsupported or missing schema_version (expected 1)")

    case_id = raw.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise RecordError("case_id must be a non-empty string")

    lane = raw.get("lane")
    if not isinstance(lane, str) or lane.lower() not in LANES:
        raise RecordError(f"lane must be one of {LANES}")
    lane = lane.lower()

    version = raw.get("squad_version")
    if not isinstance(version, str) or not version.strip():
        raise RecordError("squad_version must be a non-empty string")

    expected_raw = raw.get("expected")
    if not isinstance(expected_raw, Mapping):
        raise RecordError("expected must be an object")
    expected = _parse_expected(expected_raw, lane)

    raw_events = raw.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise RecordError("events must be a non-empty list")

    events: list[Event] = []
    for index, item in enumerate(raw_events):
        if not isinstance(item, Mapping):
            raise RecordError(f"event {index} must be an object")
        event_type = item.get("type")
        if not isinstance(event_type, str) or not event_type.strip():
            raise RecordError(f"event {index} needs a non-empty 'type'")
        events.append(
            Event(
                index=index,
                t=_parse_time(item.get("t"), index),
                type=event_type.strip(),
                data=item,
            )
        )

    if any(b.t < a.t for a, b in zip(events, events[1:])):
        raise RecordError("events must be in chronological order")

    return Record(
        case_id=case_id,
        lane=lane,
        squad_version=version,
        expected=expected,
        events=tuple(events),
        source=source,
    )
