"""Offline scorer prototype for KAR squad simulations.

Public API:
    from kareval import load_record, analyze, render_markdown
"""

from .metrics import Analysis, analyze
from .model import Record, RecordError, load_record
from .report import render_markdown
from .rules import Violation

__all__ = [
    "Analysis",
    "Record",
    "RecordError",
    "Violation",
    "analyze",
    "load_record",
    "render_markdown",
]
