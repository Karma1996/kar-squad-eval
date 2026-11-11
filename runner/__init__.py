"""Simulation runner for KAR squad evaluation.

The runner is deliberately split into four seams so each can be exercised
offline:

- ``platform``  – what Multica can do (protocol + CLI adapter + fake)
- ``contract``  – how platform facts become scored events
- ``sim_human`` – the scripted Decision Owner
- ``loop``      – polling, timeout and record assembly

Only stdlib is used. Import ``kareval`` for scoring; the runner never scores
by itself.
"""

from .clock import Clock, FakeClock, RealClock
from .platform import Comment, FakePlatform, Issue, Platform
from .scenario import Scenario, ScenarioError, load_scenario

__all__ = [
    "Clock",
    "Comment",
    "FakeClock",
    "FakePlatform",
    "Issue",
    "Platform",
    "RealClock",
    "Scenario",
    "ScenarioError",
    "load_scenario",
]
