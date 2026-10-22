""" Tablib - the plugin manager.

The manager owns the whole lifecycle and, more importantly, the *policies*
around failure:

* a plugin that cannot be imported, does not satisfy the contract, or claims a
  forbidden key is rejected with a diagnostic - the host keeps working;
* a plugin that raises while the host is calling it is reported, counted, and
  eventually quarantined (unregistered, attributes removed);
* a configuration file that is malformed degrades to defaults with a
  diagnostic instead of breaking ``import tablib``.

Everything here is additive: the upstream registry, ``Dataset`` and
``Databook`` keep their behaviour when no plugin is installed.
"""

__lazy_modules__ = {
    "threading",
    "tablib.core",
    "tablib.formats",
}

import threading

from ..exceptions import InvalidFormatPlugin, PluginConflict
from .config import Settings, load_settings
from .discovery import discover
from .guard import make_guarded
from .protocol import extract_formats, instantiate, validate_format
from .spec import Diagnostic, Phase, Record, State, format_exception


class PluginManager:
    """Discovery, loading, configuration and isolation of tablib formats."""

    def __init__(self):
        self._lock = threading.RLock()
        self._loaded = False
        self._loading = False
        self._settings = Settings()
        self._records = {}
        self._rejected = []
        self._diagnostics = []
        self._quarantined = {}
        self._discovered = 0

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def settings(self):
        self.ensure_loaded()
        return self._settings

    @property
    def strict(self):
        return self._settings.strict

    @property
    def max_failures(self):
        return self._settings.max_failures

    @property
    def discovered(self):
        return self._discovered

    def diagnostics(self):
        self.ensure_loaded()
        return tuple(self._diagnostics)

    def records(self):
        self.ensure_loaded()
        with self._lock:
            active = list(self._records.values())
            return sorted(active + list(self._rejected), key=lambda r: (r.key, r.state.value))

    def registered_keys(self):
        self.ensure_loaded()
        return sorted(self._records)

    def quarantine_reason(self, key):
        return self._quarantined.get(key)

    def to_dict(self):
        self.ensure_loaded()
        return {
            "settings": {
                "source": self._settings.source,
                "enabled": self._settings.enabled,
                "entry_points": self._settings.entry_points,
                "strict": self._settings.strict,
                "allow_override": self._settings.allow_override,
                "max_failures": self._settings.max_failures,
                "paths": list(self._settings.paths),
                "disabled": sorted(self._settings.disabled),
                "priority": dict(self._settings.priority),
                "options": sorted(self._settings.options),
            },
            "discovered": self._discovered,
            "formats": [record.to_dict() for record in self.records()],
            "diagnostics": [item.to_dict() for item in self._diagnostics],
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self, force=False):
        """Run the load pass.  ``force`` re-reads config and plugins."""
        if force:
            self.reset()
        self.ensure_loaded()

    def ensure_loaded(self):
        """Run the load pass at most once, and never raise."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded or self._loading:
                return
            self._loading = True
            try:
                self._run()
            except KeyboardInterrupt:
                raise
            except BaseException as exc:  # the plugin system must never break the host
                self._diagnose(
                    "<plugin-manager>", Phase.DISCOVERY, exc,
                    message="plugin loading aborted",
                )
            finally:
                self._loading = False
                self._loaded = True

    def reset(self):
        """Detach every plugin format and forget all state (used by tests)."""
        with self._lock:
            for key, record in list(self._records.items()):
                if record.state is State.ACTIVE:
                    self._detach(key, record)
            self._records.clear()
            self._rejected = []
            self._diagnostics = []
            self._quarantined.clear()
            self._settings = Settings()
            self._discovered = 0
            self._loaded = False
            self._loading = False

    # ------------------------------------------------------------------
    # Load pass
    # ------------------------------------------------------------------

    def _run(self):
        settings, diagnostics = load_settings()
        self._diagnostics.extend(diagnostics)
        self._settings = settings
        if not settings.enabled:
            return

        candidates = discover(settings, self._diagnostics)
        self._discovered = len(candidates)

        pending = []
        for candidate in candidates:
            for fmt in self._import_candidate(candidate):
                pending.append((candidate, fmt))

        # Registration order is auto-detection order, so ordering is policy:
        # configured priority wins, discovery order breaks ties (stable sort).
        pending.sort(key=lambda item: settings.priority_for(item[1].title, item[0].label))

        for candidate, fmt in pending:
            self._commit(candidate, fmt)

    def _import_candidate(self, candidate):
        try:
            loaded = candidate.loader()
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            self._diagnose(
                candidate.label, Phase.IMPORT, exc, origin=candidate.origin,
                message=f"could not import plugin {candidate.label!r}",
            )
            self._reject(candidate, State.REJECTED, f"import failed: {type(exc).__name__}")
            return []

        try:
            objects = extract_formats(loaded)
        except InvalidFormatPlugin as exc:
            self._diagnose(
                candidate.label, Phase.VALIDATION, exc, origin=candidate.origin,
                message=str(exc),
            )
            self._reject(candidate, State.REJECTED, str(exc))
            return []
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            self._diagnose(
                candidate.label, Phase.VALIDATION, exc, origin=candidate.origin,
                message="plugin raised while listing its formats",
            )
            self._reject(candidate, State.REJECTED, "format listing failed")
            return []

        formats = []
        for obj in objects:
            try:
                fmt = validate_format(instantiate(obj, label=candidate.label),
                                      label=candidate.label)
            except InvalidFormatPlugin as exc:
                self._diagnose(
                    candidate.label, Phase.VALIDATION, exc, origin=candidate.origin,
                    message=str(exc),
                )
                self._reject(candidate, State.REJECTED, str(exc))
                continue
            except KeyboardInterrupt:
                raise
            except BaseException as exc:
                self._diagnose(
                    candidate.label, Phase.VALIDATION, exc, origin=candidate.origin,
                    message="format instantiation failed",
                )
                self._reject(candidate, State.REJECTED, "instantiation failed")
                continue
            formats.append(fmt)
        return formats

    def _commit(self, candidate, fmt):
        key = fmt.title
        label = candidate.label

        if key in self._settings.disabled or label in self._settings.disabled:
            self._records[key] = Record(
                key=key, label=label, origin=candidate.origin, source=candidate.source,
                state=State.DISABLED, detail="disabled by configuration",
            )
            return

        conflict = self._conflict(key)
        if conflict is not None:
            self._diagnose(
                label, Phase.REGISTRATION, conflict, origin=candidate.origin,
                message=str(conflict),
            )
            self._reject(candidate, State.REJECTED, str(conflict), key=key)
            return

        options = self._settings.options.get(key) or self._settings.options.get(label) or {}
        record = Record(
            key=key, label=label, origin=candidate.origin, source=candidate.source,
            options=dict(options),
        )
        if options and not self._configure(fmt, options, record):
            return

        guard = make_guarded(fmt, record, self)
        try:
            from ..formats import registry

            record.previous = registry.peek(key)
            registry.register(key, guard, replace=self._settings.allow_override)
        except Exception as exc:
            self._diagnose(
                label, Phase.REGISTRATION, exc, origin=candidate.origin,
                message=f"could not register format {key!r}",
            )
            record.state = State.REJECTED
            record.detail = str(exc)
            self._rejected.append(record)
            return

        record.format = guard
        self._records[key] = record

    def _configure(self, fmt, options, record):
        configure = getattr(fmt, "configure", None)
        try:
            if callable(configure):
                configure(dict(options))
            else:
                fmt.plugin_options = dict(options)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            self._diagnose(
                record.key, Phase.CONFIGURATION, exc, origin=record.origin,
                message=f"configure() rejected the configured options for {record.key!r}",
            )
            record.state = State.REJECTED
            record.detail = f"configuration failed: {type(exc).__name__}"
            self._rejected.append(record)
            return False
        return True

    def _conflict(self, key):
        """Return the reason why ``key`` cannot be registered, or ``None``."""
        from ..core import Databook, Dataset
        from ..formats import registry

        if not registry.has(key):
            # A plugin must never be able to shadow an unrelated Dataset method
            # (``export``, ``filter``, ...) - that would break the host itself.
            for name in (key, f"get_{key}", f"set_{key}"):
                if hasattr(Dataset, name) or hasattr(Databook, name):
                    return PluginConflict(
                        f"key {key!r} collides with the existing Dataset attribute "
                        f"{name!r}; refusing to register even with allow_override."
                    )
            return None

        if self._settings.allow_override:
            return None
        return PluginConflict(
            f"format {key!r} is already registered; set allow_override = true to replace it."
        )

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def record_failure(self, record, method, exc):
        """Count a runtime failure and quarantine the plugin if needed."""
        with self._lock:
            record.failures += 1
            self._diagnose(
                record.key, Phase.EXECUTION, exc, origin=record.origin,
                message=f"{method}() raised {type(exc).__name__}",
            )
            if record.state is State.ACTIVE and record.failures >= self._settings.max_failures:
                self._quarantine(record, f"{record.failures} failure(s), last one in {method}()")
                return record.failures, True
            return record.failures, False

    def _quarantine(self, record, reason):
        record.state = State.QUARANTINED
        record.detail = reason
        self._quarantined[record.key] = reason
        self._detach(record.key, record)
        self._diagnostics.append(Diagnostic(
            plugin=record.key,
            phase=Phase.EXECUTION,
            error_type="PluginQuarantined",
            message=f"format {record.key!r} was unregistered: {reason}",
            origin=record.origin,
        ))

    def _detach(self, key, record):
        from ..formats import registry

        if registry.peek(key) is not record.format:
            return
        if record.previous is not None:
            # An overriding plugin goes away -> the built-in format comes back.
            registry.register(key, record.previous, replace=True)
        else:
            registry.unregister(key)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _diagnose(self, plugin, phase, exc, message="", origin=""):
        text = message or str(exc)
        if message and str(exc) and str(exc) not in message:
            text = f"{message}: {exc}"
        self._diagnostics.append(Diagnostic(
            plugin=plugin,
            phase=phase,
            error_type=type(exc).__name__,
            message=text,
            origin=origin,
            traceback=format_exception(exc),
        ))

    def _reject(self, candidate, state, detail, key=None):
        self._rejected.append(Record(
            key=key or candidate.label,
            label=candidate.label,
            origin=candidate.origin,
            source=candidate.source,
            state=state,
            detail=detail,
        ))


_manager = PluginManager()


def get_manager():
    return _manager


def ensure_loaded():
    _manager.ensure_loaded()


def load(force=False):
    _manager.load(force=force)


def reset():
    _manager.reset()


def diagnostics():
    return _manager.diagnostics()


def records():
    return _manager.records()


def settings():
    return _manager.settings


def quarantine_reason(key):
    return _manager.quarantine_reason(key)


def doctor():
    """Return a JSON-serialisable health report."""
    data = _manager.to_dict()
    broken = [
        item for item in data["formats"]
        if item["state"] in (State.REJECTED.value, State.QUARANTINED.value)
    ]
    data["ok"] = not broken and not any(
        item["phase"] in (Phase.IMPORT.value, Phase.VALIDATION.value)
        and item["error_type"] != "PathNotFound"
        for item in data["diagnostics"]
    )
    return data
