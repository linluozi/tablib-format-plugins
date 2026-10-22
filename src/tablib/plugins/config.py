""" Tablib - plugin configuration.

Configuration is optional: with no file, no environment variable and no
configured search path the plugin subsystem discovers only the formats that
were installed with a ``tablib.formats`` entry point, i.e. the classic
``pip install tablib-something`` workflow.

Resolution order (first file that exists wins):

1. an explicit path passed to :func:`load_settings`;
2. ``$TABLIB_PLUGIN_CONFIG``;
3. ``%APPDATA%\\tablib\\plugins.toml`` (Windows only);
4. ``$XDG_CONFIG_HOME/tablib/plugins.toml``;
5. ``~/.config/tablib/plugins.toml``;
6. ``./tablib-plugins.toml`` (project-local).

A JSON file is accepted as well (``.json`` suffix).  Environment variables are
applied *after* the file, so a deployment can override a checked-in config
without editing it.
"""

__lazy_modules__ = {
    "json",
    "os",
    "pathlib",
    "sys",
    "tomllib",
}

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..exceptions import PluginConfigError
from .spec import Diagnostic, Phase

CONFIG_ENV_VAR = "TABLIB_PLUGIN_CONFIG"
PATH_ENV_VAR = "TABLIB_PLUGIN_PATH"
DISABLED_ENV_VAR = "TABLIB_PLUGINS_DISABLE"
ENABLED_ENV_VAR = "TABLIB_PLUGINS_ENABLED"
ENTRY_POINTS_ENV_VAR = "TABLIB_PLUGINS_ENTRY_POINTS"
STRICT_ENV_VAR = "TABLIB_PLUGINS_STRICT"
MAX_FAILURES_ENV_VAR = "TABLIB_PLUGINS_MAX_FAILURES"

DEFAULT_MAX_FAILURES = 3
DEFAULT_PRIORITY = 100

_TOP_LEVEL_KEYS = frozenset({
    "enabled",
    "entry_points",
    "strict",
    "allow_override",
    "max_failures",
    "paths",
    "disabled",
    "priority",
    "options",
})

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class Settings:
    """Effective plugin settings after file + environment merging."""

    enabled: bool = True
    entry_points: bool = True
    strict: bool = False
    allow_override: bool = False
    max_failures: int = DEFAULT_MAX_FAILURES
    paths: tuple = ()
    disabled: frozenset = frozenset()
    priority: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    source: str = ""

    def priority_for(self, *names):
        """Lowest priority number configured for any of ``names``."""
        found = [self.priority[n] for n in names if n in self.priority]
        return min(found) if found else DEFAULT_PRIORITY

    def with_changes(self, **kwargs):
        data = {
            "enabled": self.enabled,
            "entry_points": self.entry_points,
            "strict": self.strict,
            "allow_override": self.allow_override,
            "max_failures": self.max_failures,
            "paths": self.paths,
            "disabled": self.disabled,
            "priority": self.priority,
            "options": self.options,
            "source": self.source,
        }
        data.update(kwargs)
        return Settings(**data)


def _as_bool(value, key):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise PluginConfigError(f"'{key}' must be a boolean, got {value!r}.")


def _as_positive_int(value, key):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PluginConfigError(f"'{key}' must be an integer, got {value!r}.")
    if value < 1:
        raise PluginConfigError(f"'{key}' must be >= 1, got {value!r}.")
    return value


def _as_str_tuple(value, key, base=None):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise PluginConfigError(f"'{key}' must be a list of strings, got {value!r}.")
    out = []
    for item in value:
        if not isinstance(item, str):
            raise PluginConfigError(f"'{key}' must contain strings, got {item!r}.")
        out.append(_resolve_path(item, base))
    return tuple(out)


def _as_int_table(value, key):
    if not isinstance(value, dict):
        raise PluginConfigError(f"'{key}' must be a table of integers, got {value!r}.")
    out = {}
    for name, number in value.items():
        if isinstance(number, bool) or not isinstance(number, int):
            raise PluginConfigError(f"'{key}.{name}' must be an integer, got {number!r}.")
        out[str(name)] = number
    return out


def _as_options_table(value, key="options"):
    if not isinstance(value, dict):
        raise PluginConfigError(f"'{key}' must be a table of tables, got {value!r}.")
    out = {}
    for name, table in value.items():
        if not isinstance(table, dict):
            raise PluginConfigError(f"'{key}.{name}' must be a table, got {table!r}.")
        out[str(name)] = dict(table)
    return out


def _resolve_path(raw, base=None):
    expanded = os.path.expandvars(os.path.expanduser(raw))
    path = Path(expanded)
    if not path.is_absolute() and base is not None:
        path = Path(base) / path
    return str(path)


def _read_raw(path):
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix == ".json":
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise PluginConfigError(f"{path}: invalid JSON ({err}).") from err
    try:
        import tomllib
    except ImportError:  # Python 3.10
        try:
            import tomli as tomllib
        except ImportError as err:
            raise PluginConfigError(
                f"{path}: reading TOML requires Python 3.11+ or the 'tomli' package."
            ) from err
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise PluginConfigError(f"{path}: invalid TOML ({err}).") from err


def parse_config(raw, source=""):
    """Turn a decoded config document into a :class:`Settings` instance.

    Raises :class:`PluginConfigError` on any schema violation.  The plugin
    manager converts that error into a diagnostic so a typo can never make
    ``import tablib`` fail.
    """
    if not isinstance(raw, dict):
        raise PluginConfigError(f"{source}: top level must be a table.")

    table = raw.get("plugins", raw)
    if not isinstance(table, dict):
        raise PluginConfigError(f"{source}: 'plugins' must be a table.")

    unknown = sorted(set(table) - _TOP_LEVEL_KEYS)
    if unknown:
        raise PluginConfigError(
            f"{source}: unknown key(s) in [plugins]: {', '.join(unknown)}."
        )

    base = Path(source).parent if source else None
    kwargs = {"source": source}

    if "enabled" in table:
        kwargs["enabled"] = _as_bool(table["enabled"], "enabled")
    if "entry_points" in table:
        kwargs["entry_points"] = _as_bool(table["entry_points"], "entry_points")
    if "strict" in table:
        kwargs["strict"] = _as_bool(table["strict"], "strict")
    if "allow_override" in table:
        kwargs["allow_override"] = _as_bool(table["allow_override"], "allow_override")
    if "max_failures" in table:
        kwargs["max_failures"] = _as_positive_int(table["max_failures"], "max_failures")
    if "paths" in table:
        kwargs["paths"] = _as_str_tuple(table["paths"], "paths", base)
    if "disabled" in table:
        disabled = table["disabled"]
        if isinstance(disabled, str):
            disabled = [disabled]
        if not isinstance(disabled, (list, tuple)) or not all(
            isinstance(item, str) for item in disabled
        ):
            raise PluginConfigError("'disabled' must be a list of strings.")
        kwargs["disabled"] = frozenset(disabled)
    if "priority" in table:
        kwargs["priority"] = _as_int_table(table["priority"], "priority")
    if "options" in table:
        kwargs["options"] = _as_options_table(table["options"])

    return Settings(**kwargs)


def default_config_candidates(environ=None):
    env = os.environ if environ is None else environ
    candidates = []
    appdata = env.get("APPDATA")
    if sys.platform == "win32" and appdata:
        candidates.append(Path(appdata) / "tablib" / "plugins.toml")
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        candidates.append(Path(xdg) / "tablib" / "plugins.toml")
    candidates.append(Path.home() / ".config" / "tablib" / "plugins.toml")
    candidates.append(Path.cwd() / "tablib-plugins.toml")
    return candidates


def _env_bool(env, name, diagnostics, default):
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    text = raw.strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    diagnostics.append(Diagnostic(
        plugin="<environment>",
        phase=Phase.CONFIG,
        error_type="InvalidEnvironmentValue",
        message=f"{name}={raw!r} is not a boolean, ignoring it.",
    ))
    return default


def apply_environment(settings, environ=None):
    """Return ``(settings, diagnostics)`` after applying env overrides."""
    env = os.environ if environ is None else environ
    diagnostics = []
    changes = {}

    changes["enabled"] = _env_bool(env, ENABLED_ENV_VAR, diagnostics, settings.enabled)
    changes["entry_points"] = _env_bool(
        env, ENTRY_POINTS_ENV_VAR, diagnostics, settings.entry_points
    )
    changes["strict"] = _env_bool(env, STRICT_ENV_VAR, diagnostics, settings.strict)

    raw_max = env.get(MAX_FAILURES_ENV_VAR)
    if raw_max:
        try:
            changes["max_failures"] = _as_positive_int(int(raw_max), MAX_FAILURES_ENV_VAR)
        except (ValueError, PluginConfigError):
            diagnostics.append(Diagnostic(
                plugin="<environment>",
                phase=Phase.CONFIG,
                error_type="InvalidEnvironmentValue",
                message=f"{MAX_FAILURES_ENV_VAR}={raw_max!r} is not a positive integer.",
            ))

    raw_paths = env.get(PATH_ENV_VAR)
    if raw_paths:
        extra = tuple(
            _resolve_path(part) for part in raw_paths.split(os.pathsep) if part.strip()
        )
        changes["paths"] = tuple(settings.paths) + extra

    raw_disabled = env.get(DISABLED_ENV_VAR)
    if raw_disabled:
        extra = {part.strip() for part in raw_disabled.split(",") if part.strip()}
        changes["disabled"] = frozenset(settings.disabled | extra)

    return settings.with_changes(**changes), diagnostics


def load_settings(path=None, environ=None):
    """Return ``(Settings, diagnostics)``; never raises for bad user input."""
    env = os.environ if environ is None else environ
    diagnostics = []
    settings = Settings()

    explicit = path or env.get(CONFIG_ENV_VAR)
    chosen = None
    if explicit:
        candidate = Path(_resolve_path(explicit))
        if candidate.is_file():
            chosen = candidate
        else:
            diagnostics.append(Diagnostic(
                plugin="<config>",
                phase=Phase.CONFIG,
                error_type="ConfigNotFound",
                message=f"Configured plugin file does not exist: {candidate}",
                origin=str(candidate),
            ))
    else:
        for candidate in default_config_candidates(env):
            if candidate.is_file():
                chosen = candidate
                break

    if chosen is not None:
        try:
            settings = parse_config(_read_raw(chosen), source=str(chosen))
        except PluginConfigError as err:
            diagnostics.append(Diagnostic(
                plugin="<config>",
                phase=Phase.CONFIG,
                error_type=type(err).__name__,
                message=str(err),
                origin=str(chosen),
            ))
            settings = Settings(source=str(chosen))

    settings, env_diagnostics = apply_environment(settings, env)
    diagnostics.extend(env_diagnostics)
    return settings, diagnostics
