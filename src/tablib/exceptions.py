class TablibException(Exception):
    """Tablib common exception."""


class InvalidDatasetType(TablibException, TypeError):
    """Only Datasets can be added to a Databook."""


class InvalidDimensions(TablibException, ValueError):
    """The size of the column or row doesn't fit the table dimensions."""


class InvalidDatasetIndex(TablibException, IndexError):
    """Outside of Dataset size."""


class HeadersNeeded(TablibException, AttributeError):
    """Header parameter must be given when appending a column to this Dataset."""


class UnsupportedFormat(TablibException, NotImplementedError):
    """Format not supported."""


class PluginError(TablibException):
    """Base class for every plugin subsystem error."""


class PluginConfigError(PluginError, ValueError):
    """The plugin configuration file is unreadable, malformed or out of schema."""


class PluginLoadError(PluginError, ImportError):
    """A plugin could not be imported."""


class InvalidFormatPlugin(PluginError, TypeError):
    """An imported object does not satisfy the tablib format contract."""


class PluginConflict(PluginError, ValueError):
    """A plugin wants a registry key that is already taken or reserved."""


class PluginExecutionError(PluginError, RuntimeError):
    """A loaded plugin raised while the host was calling it."""


class PluginQuarantined(UnsupportedFormat):
    """A plugin was unloaded by the circuit breaker after repeated failures."""
