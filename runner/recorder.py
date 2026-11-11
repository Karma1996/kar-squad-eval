"""Assemble the immutable ``record.json`` the scorer reads.

The recorder only writes what the platform and the observers reported. It
never copies a self-reported metric from the squad; every event carries its
source timestamp and actor type.

Events are stable-sorted by time so a slow poll cannot reorder facts that the
platform already timestamped.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scenario import Scenario

SCHEMA_VERSION = 1


class RecordBuilder:
    def __init__(
        self,
        scenario: Scenario,
        squad_version: str = "4.4.0",
        started_at: datetime | None = None,
    ) -> None:
        self.scenario = scenario
        self.squad_version = squad_version
        self.started_at = started_at or datetime.now(timezone.utc)
        self.events: list[dict[str, Any]] = []
        self._dedupe: set[str] = set()

    def add(
        self,
        event_type: str,
        at: datetime | None = None,
        *,
        dedupe_key: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        if dedupe_key:
            if dedupe_key in self._dedupe:
                return {}
            self._dedupe.add(dedupe_key)
        event = {
            "t": (at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            "type": event_type,
            "source": "multica",
        }
        event.update(fields)
        self.events.append(event)
        return event

    def finalize(self, out: str | Path | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "schema_version": SCHEMA_VERSION,
            "case_id": self.scenario.id,
            "lane": self.scenario.lane,
            "squad_version": self.squad_version,
            "expected": self.expected(),
            "events": self._sorted_events(),
        }
        if meta:
            record["runner"] = meta
        if out is not None:
            path = Path(out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return record

    def expected(self) -> dict[str, Any]:
        expected = dict(self.scenario.expected)
        expected["lane"] = self.scenario.lane
        expected.setdefault("required_gates", list(self.scenario.required_gates))
        expected.setdefault("max_gate_cards", self.scenario.max_gate_cards)
        expected.setdefault("human_touch_budget", self.scenario.human_touch_budget)
        expected.setdefault("docs_required", [])
        expected.setdefault("require_ci", True)
        return expected

    def _sorted_events(self) -> list[dict[str, Any]]:
        def key(event: dict[str, Any]) -> str:
            return str(event.get("t", ""))

        return sorted(self.events, key=key)
