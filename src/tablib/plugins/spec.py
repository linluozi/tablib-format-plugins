""" Tablib - plugin data model.

The plugin subsystem moves every external format through four phases:

``discovery`` -> ``import`` -> ``validation``/``registration`` -> ``execution``

Each phase is owned by a different module, but they all speak the small data
model defined here.  Keeping the model free of behaviour makes it safe to
import from any of them (no import cycles) and easy to serialise for
``python -m tablib.plugins --json``.
"""

__lazy_modules__ = {"collections.abc", "dataclasses", "enum", "traceback", "typing"}

import traceback as _traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    """Where in the plugin lifecycle a diagnostic was produced."""

    CONFIG = "config"
    DISCOVERY = "discovery"
    IMPORT = "import"
    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    REGISTRATION = "registration"
    EXECUTION = "execution"


class State(str, Enum):
    """Final state of a plugin after the load pass."""

    ACTIVE = "active"
    DISABLED = "disabled"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class Source(str, Enum):
    """How a candidate was discovered."""

    DIRECTORY = "directory"
    ENTRY_POINT = "entry-point"
    MANUAL = "manual"


@dataclass(frozen=True)
class Candidate:
    """A discovered, not yet imported plugin.

    ``loader`` is a zero-argument callable performing the import.  Deferring it
    keeps discovery free of side effects and lets the manager attribute an
    import failure to the exact candidate that caused it.
    """

    label: str
    origin: str
    source: Source
    loader: Callable[[], Any]
    priority: int = 100

    @property
    def sort_key(self):
        return (self.priority, self.label, self.origin)


@dataclass
class Diagnostic:
    """A single recoverable failure or warning."""

    plugin: str
    phase: Phase
    error_type: str
    message: str
    origin: str = ""
    traceback: str = ""

    def to_dict(self):
        return {
            "plugin": self.plugin,
            "phase": self.phase.value,
            "error_type": self.error_type,
            "message": self.message,
            "origin": self.origin,
            "traceback": self.traceback,
        }

    def __str__(self):
        where = f" ({self.origin})" if self.origin else ""
        return f"[{self.phase.value}] {self.plugin}{where}: {self.error_type}: {self.message}"


@dataclass
class Record:
    """What the manager remembers about one registered format key."""

    key: str
    label: str
    origin: str
    source: Source
    state: State = State.ACTIVE
    failures: int = 0
    format: Any = None
    options: dict = field(default_factory=dict)
    detail: str = ""
    previous: Any = None

    def to_dict(self):
        return {
            "key": self.key,
            "label": self.label,
            "origin": self.origin,
            "source": self.source.value,
            "state": self.state.value,
            "failures": self.failures,
            "options": dict(self.options),
            "detail": self.detail,
        }


def format_exception(exc):
    """Return a compact traceback string for a captured exception."""
    return "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
