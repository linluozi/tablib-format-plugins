""" Tablib - plugin subsystem.

Public entry points::

    >>> import tablib
    >>> tablib.plugins.load()               # explicit load pass
    >>> tablib.plugins.records()            # what got registered
    >>> tablib.plugins.diagnostics()        # what failed and why
    >>> tablib.plugins.doctor()["ok"]       # health report for ops

Loading is lazy: the first use of an unknown format (or the first
auto-detection) runs the load pass, so a format installed as an entry point is
usable without any explicit call::

    >>> dataset.export("markdown")          # doctest: +SKIP

Set ``TABLIB_PLUGINS_ENABLED=0`` to make the subsystem a no-op.
"""

__lazy_modules__ = {
    "tablib.plugins.config",
    "tablib.plugins.discovery",
    "tablib.plugins.guard",
    "tablib.plugins.manager",
    "tablib.plugins.protocol",
    "tablib.plugins.spec",
}

from .config import Settings, load_settings, parse_config
from .manager import (
    diagnostics,
    doctor,
    ensure_loaded,
    get_manager,
    load,
    quarantine_reason,
    records,
    reset,
    settings,
)
from .spec import Diagnostic, Phase, Record, Source, State

__all__ = [
    "Diagnostic",
    "Phase",
    "Record",
    "Settings",
    "Source",
    "State",
    "diagnostics",
    "doctor",
    "ensure_loaded",
    "get_manager",
    "load",
    "load_settings",
    "parse_config",
    "quarantine_reason",
    "records",
    "registered_keys",
    "reset",
    "settings",
]


def registered_keys():
    """Keys registered by the plugin subsystem (built-in keys excluded)."""
    return get_manager().registered_keys()
