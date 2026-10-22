""" Tablib - runtime guard for plugin formats.

Every plugin format is wrapped in a generated proxy before it enters the
registry.  The proxy is what makes "error isolation" concrete:

* only the methods the plugin actually implements are exposed, so
  ``hasattr(fmt, 'import_set')`` keeps telling the truth for the host's
  ``Dataset.load`` path;
* every call is wrapped; a failing call is recorded and re-raised as
  :class:`~tablib.exceptions.PluginExecutionError` with the plugin identity;
* ``detect()`` is treated differently on purpose.  It is a heuristic probe run
  over untrusted input during auto-detection; a probe that explodes means "this
  format does not match", not "abort the import".  It therefore returns
  ``False`` unless ``strict`` is configured;
* after ``max_failures`` failures the manager quarantines the format, which
  unregisters it from the registry.

``__getattr__`` delegates everything else (``DEFAULT_DELIMITER``, helpers) to
the wrapped object so format-specific options still work.
"""

__lazy_modules__ = {"typing"}

from ..exceptions import PluginExecutionError
from .protocol import ALL_METHODS, PROBE_METHODS


class _GuardBase:
    __slots__ = ("_target", "_record", "_manager")

    def __init__(self, target, record, manager):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_record", record)
        object.__setattr__(self, "_manager", manager)

    def __getattr__(self, name):
        return getattr(self._target, name)

    def __repr__(self):
        return (
            f"<guarded format {self._record.key!r} from {self._record.origin}>"
        )

    def _invoke(self, name, args, kwargs):
        call = getattr(self._target, name)
        try:
            return call(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            failures, quarantined = self._manager.record_failure(self._record, name, exc)
            if name in PROBE_METHODS and not self._manager.strict:
                return False
            if quarantined:
                hint = " The plugin has been quarantined and its format unregistered."
            else:
                remaining = max(self._manager.max_failures - failures, 0)
                hint = f" It will be quarantined after {remaining} more failure(s)."
            raise PluginExecutionError(
                f"Plugin format {self._record.key!r} ({self._record.origin}) failed in "
                f"{name}(): {type(exc).__name__}: {exc}.{hint}"
            ) from exc


def _guard_method(name):
    def method(self, *args, **kwargs):
        return self._invoke(name, args, kwargs)

    method.__name__ = name
    method.__qualname__ = name
    method.__doc__ = f"Guarded call into the plugin's {name}()."
    return method


def make_guarded(target, record, manager):
    """Build the proxy that will be registered in place of ``target``."""
    namespace = {
        "__doc__": f"Guarded tablib format {record.key!r}.",
        "title": record.key,
        "extensions": tuple(getattr(target, "extensions", ()) or ()),
    }
    for name in ALL_METHODS:
        if callable(getattr(target, name, None)):
            namespace[name] = _guard_method(name)
    proxy_type = type(f"GuardedFormat_{record.key}", (_GuardBase,), namespace)
    return proxy_type(target, record, manager)
