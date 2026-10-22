""" Tablib - plugin discovery.

Two discovery channels are supported:

``entry points``
    Any installed distribution may declare ``[project.entry-points."tablib.formats"]``.
    This is the packaging-friendly channel: ``pip install tablib-markdown`` is
    enough for the format to appear.

``search path``
    Directories (and single ``.py`` files) listed in ``plugins.paths`` or in
    ``$TABLIB_PLUGIN_PATH``.  This is the "drop a file in a folder" channel,
    useful for a project-local format that is not worth publishing.

Discovery performs *no* imports: every candidate carries a ``loader`` callable
so that an import failure can be attributed to the exact candidate instead of
aborting the whole pass.
"""

__lazy_modules__ = {
    "hashlib",
    "importlib",
    "importlib.metadata",
    "importlib.util",
    "re",
    "sys",
    "pathlib",
}

import hashlib
import importlib.metadata
import importlib.util
import re
import sys
from pathlib import Path

from .spec import Candidate, Diagnostic, Phase, Source, format_exception

ENTRY_POINT_GROUP = "tablib.formats"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_]")


def _entry_points():
    """Return the entry points declared for :data:`ENTRY_POINT_GROUP`."""
    found = importlib.metadata.entry_points()
    if hasattr(found, "select"):  # Python 3.10+
        return list(found.select(group=ENTRY_POINT_GROUP))
    return list(found.get(ENTRY_POINT_GROUP, []))  # pragma: no cover


def _module_name_for(path, is_package):
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    stem = path.name if is_package else path.stem
    return f"tablib_plugin_{_SAFE_NAME.sub('_', stem)}_{digest}"


def import_module_from_file(path, is_package=False):
    """Import ``path`` under a private, collision-free module name."""
    path = Path(path)
    module_name = _module_name_for(path, is_package)
    if is_package:
        spec = importlib.util.spec_from_file_location(
            module_name, path / "__init__.py", submodule_search_locations=[str(path)]
        )
    else:
        spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot build an import spec for {path}.")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses/pickle and intra-package imports
    # need to find the module in sys.modules.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def entry_point_candidates(settings, diagnostics):
    candidates = []
    seen = set()
    try:
        entry_points = _entry_points()
    except Exception as err:  # a broken distribution metadata must not crash us
        diagnostics.append(Diagnostic(
            plugin="<entry-points>",
            phase=Phase.DISCOVERY,
            error_type=type(err).__name__,
            message=f"Could not read installed entry points: {err}",
            traceback=format_exception(err),
        ))
        return candidates

    for entry_point in sorted(entry_points, key=lambda ep: ep.name):
        signature = (entry_point.name, getattr(entry_point, "value", ""))
        if signature in seen:
            continue
        seen.add(signature)
        dist = getattr(entry_point, "dist", None)
        origin = f"entry point {entry_point.group}:{entry_point.name} -> {entry_point.value}"
        if dist is not None and getattr(dist, "name", None):
            origin = f"{dist.name} ({entry_point.value})"
        candidates.append(Candidate(
            label=entry_point.name,
            origin=origin,
            source=Source.ENTRY_POINT,
            loader=entry_point.load,
            priority=settings.priority_for(entry_point.name),
        ))
    return candidates


def directory_candidates(settings, diagnostics):
    candidates = []
    seen_paths = set()
    for raw in settings.paths:
        path = Path(raw)
        if not path.exists():
            diagnostics.append(Diagnostic(
                plugin="<search-path>",
                phase=Phase.DISCOVERY,
                error_type="PathNotFound",
                message=f"Plugin search path does not exist: {raw}",
                origin=raw,
            ))
            continue
        if path.is_file():
            targets = [(path, False)]
        elif path.is_dir():
            targets = []
            try:
                entries = sorted(path.iterdir(), key=lambda item: item.name)
            except OSError as err:
                diagnostics.append(Diagnostic(
                    plugin="<search-path>",
                    phase=Phase.DISCOVERY,
                    error_type=type(err).__name__,
                    message=f"Cannot list plugin directory {raw}: {err}",
                    origin=raw,
                ))
                continue
            for entry in entries:
                if entry.name.startswith((".", "_")):
                    continue
                if entry.is_file() and entry.suffix == ".py":
                    targets.append((entry, False))
                elif entry.is_dir() and (entry / "__init__.py").is_file():
                    targets.append((entry, True))
        else:  # pragma: no cover - sockets, devices, ...
            diagnostics.append(Diagnostic(
                plugin="<search-path>",
                phase=Phase.DISCOVERY,
                error_type="UnsupportedPath",
                message=f"Plugin search path is neither a file nor a directory: {raw}",
                origin=raw,
            ))
            continue

        for target, is_package in targets:
            resolved = target.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            label = target.name if is_package else target.stem
            candidates.append(Candidate(
                label=label,
                origin=str(target),
                source=Source.DIRECTORY,
                loader=(
                    lambda target=target, is_package=is_package:
                    import_module_from_file(target, is_package)
                ),
                priority=settings.priority_for(label),
            ))
    return candidates


def discover(settings, diagnostics):
    """Return every candidate, ordered by configured priority then label."""
    candidates = list(directory_candidates(settings, diagnostics))
    if settings.entry_points:
        candidates.extend(entry_point_candidates(settings, diagnostics))
    return sorted(candidates, key=lambda candidate: candidate.sort_key)
