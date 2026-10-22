""" Tablib - the format plugin contract.

A plugin is a plain object (usually a class, sometimes a module) that looks like
the built-in formats::

    class MarkdownFormat:
        title = "markdown"           # registry key, must be a lowercase identifier
        extensions = ("md",)         # optional, used by tooling

        @classmethod
        def export_set(cls, dataset, **kwargs): ...

        @classmethod
        def import_set(cls, dset, in_stream, **kwargs): ...

        @classmethod
        def detect(cls, stream, **kwargs): ...   # optional

``export_book``/``import_book`` are optional too.  At least one of the four
transfer methods must exist, otherwise the object is not a format.

A module may expose several formats::

    TABLIB_FORMATS = [MarkdownFormat, CsvFootnotes]

or a factory::

    def get_formats(): ...

If neither is present, module-level classes defined in that module and carrying
a ``title`` plus a transfer method are picked up automatically.

The invariant that makes discovery robust: **the registry key is always the
format's own ``title``**.  ``detect_format()`` returns ``fmt.title`` and callers
feed that value back into ``get_format()``, so a plugin whose title and key
disagreed would create a format that can be detected but never loaded.
"""

__lazy_modules__ = {"inspect", "re", "types"}

import re
import types

from ..exceptions import InvalidFormatPlugin

FORMAT_METHODS = ("export_set", "import_set", "export_book", "import_book")
PROBE_METHODS = ("detect",)
ALL_METHODS = FORMAT_METHODS + PROBE_METHODS

_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _transfer_methods(obj):
    return [name for name in FORMAT_METHODS if callable(getattr(obj, name, None))]


def looks_like_format(obj):
    return bool(getattr(obj, "title", None)) and bool(_transfer_methods(obj))


def _defined_in(obj, module):
    owner = getattr(obj, "__module__", None)
    return owner == getattr(module, "__name__", None)


def extract_formats(obj):
    """Return the list of format objects exposed by an imported plugin object."""
    if not isinstance(obj, types.ModuleType):
        return [obj]

    declared = getattr(obj, "TABLIB_FORMATS", None)
    if declared is not None:
        if callable(declared) and not isinstance(declared, (list, tuple)):
            declared = declared()
        if not isinstance(declared, (list, tuple)) or not declared:
            raise InvalidFormatPlugin(
                f"{obj.__name__}: TABLIB_FORMATS must be a non-empty list."
            )
        return list(declared)

    factory = getattr(obj, "get_formats", None)
    if callable(factory):
        produced = factory()
        if not isinstance(produced, (list, tuple)) or not produced:
            raise InvalidFormatPlugin(
                f"{obj.__name__}: get_formats() must return a non-empty list."
            )
        return list(produced)

    found = [
        value for value in vars(obj).values()
        if looks_like_format(value) and _defined_in(value, obj)
    ]
    if not found:
        raise InvalidFormatPlugin(
            f"{obj.__name__}: no format found. Define a class with a 'title' and "
            "at least one of " + ", ".join(FORMAT_METHODS) + ", or list it in TABLIB_FORMATS."
        )
    return found


def instantiate(obj, label=""):
    """Turn a class into an instance; pass instances through untouched."""
    if not isinstance(obj, type):
        return obj
    try:
        return obj()
    except TypeError as err:
        raise InvalidFormatPlugin(
            f"{label or obj!r}: format class {obj.__name__} cannot be instantiated "
            f"without arguments ({err}). Use classmethods or a zero-argument __init__."
        ) from err


def validate_format(fmt, label=""):
    """Check the contract and normalise ``title``/``extensions``.

    Returns the format object.  Raises :class:`InvalidFormatPlugin` with a
    message that names the offending plugin.
    """
    where = label or type(fmt).__name__
    title = getattr(fmt, "title", None)
    if not isinstance(title, str) or not title:
        raise InvalidFormatPlugin(f"{where}: 'title' must be a non-empty string.")
    if not _KEY_PATTERN.match(title):
        raise InvalidFormatPlugin(
            f"{where}: title {title!r} is not a lowercase identifier. The title becomes "
            "the registry key and a Dataset attribute, so it must match "
            "^[a-z][a-z0-9_]*$."
        )

    methods = _transfer_methods(fmt)
    if not methods:
        raise InvalidFormatPlugin(
            f"{where}: format {title!r} implements none of {', '.join(FORMAT_METHODS)}."
        )
    for name in ALL_METHODS:
        attribute = getattr(fmt, name, None)
        if attribute is not None and not callable(attribute):
            raise InvalidFormatPlugin(f"{where}: '{name}' must be callable.")

    extensions = getattr(fmt, "extensions", ())
    if extensions is None:
        extensions = ()
    if isinstance(extensions, str):
        extensions = (extensions,)
    if not isinstance(extensions, (list, tuple)) or not all(
        isinstance(item, str) for item in extensions
    ):
        raise InvalidFormatPlugin(f"{where}: 'extensions' must be a tuple of strings.")
    try:
        fmt.extensions = tuple(item.lower().lstrip(".") for item in extensions)
    except AttributeError:  # __slots__ without 'extensions'
        pass

    return fmt
