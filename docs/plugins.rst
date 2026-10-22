Format plugins
==============

Tablib ships fifteen formats. The plugin subsystem lets a distribution add
more without forking tablib, and keeps the host working when a plugin is
broken.

Everything here is additive: a program that installs no plugin gets exactly the
same registry, the same output bytes and the same auto-detection results as
before. That equivalence is checked mechanically (``tools/diff_equivalence.py``)
rather than asserted in prose.

Why a plugin layer at all
-------------------------

``tablib.formats.registry`` already maps a format name to an object with
``export_set``/``import_set``. What it did not have was a way for code outside
the tablib source tree to put something in that mapping:

* no entry point group, so a distribution could not contribute a format;
* no search path, so a project-local format required monkeypatching;
* ``Registry.register()`` silently overwrote an existing key, so a careless
  plugin could replace ``csv`` for the whole process;
* nothing caught a plugin that raised, so an exception inside a third-party
  ``detect()`` broke every ``Dataset.load()`` in the interpreter.

The plugin subsystem adds discovery, a validated contract, configuration and a
failure policy - in that order - around the existing registry.

Quick start
-----------

Installed plugin (the normal case)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A plugin is an ordinary distribution that declares an entry point::

    [project.entry-points."tablib.formats"]
    markdown = "tablib_markdown_format:MarkdownFormat"

.. code-block:: bash

    pip install tablib-markdown-format

.. code-block:: python

    >>> import tablib
    >>> data = tablib.Dataset(headers=["name", "qty"])
    >>> data.append(["apple", 3])
    >>> print(data.export("markdown"))
    | name | qty |
    | --- | --- |
    | apple | 3 |

The first use of an unknown format name triggers the load pass, so no explicit
``tablib.plugins.load()`` call is needed.

Local plugin directory (development, or private formats)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    export TABLIB_PLUGIN_PATH=$PWD/tablib-plugins     # os.pathsep-separated

or, persistently, in ``~/.config/tablib/plugins.toml``::

    [plugins]
    paths = ["./tablib-plugins"]

Every ``*.py`` file and every package directory (a directory containing
``__init__.py``) in that folder is treated as one plugin.

Two working examples live in ``examples/``: ``tablib-markdown-format`` and
``tablib-ndjson-format``. Both are complete, installable distributions with
their own tests.

The format contract
-------------------

A plugin exposes one or more *format objects*. An object looks like the
built-in formats::

    class MarkdownFormat:
        title = "markdown"                  # registry key, required
        extensions = ("md", "markdown")     # optional

        def export_set(self, dataset, **kwargs): ...
        def import_set(self, dset, in_stream, **kwargs): ...
        def detect(self, stream, **kwargs): ...        # optional
        def configure(self, options): ...              # optional, plugin options

Rules enforced at load time:

``title``
    Required, non-empty, and matching ``^[a-z][a-z0-9_]*$``. It becomes the
    registry key *and* two ``Dataset`` attributes (``Dataset.<key>``,
    ``Dataset.get_<key>``), so it must be a usable identifier. The title is also
    what ``detect_format()`` returns, which is why the registry key is the
    title and not, say, the entry point name: a mismatch would produce a format
    that can be detected but never loaded.

transfer methods
    At least one of ``export_set``, ``import_set``, ``export_book``,
    ``import_book``. ``Dataset.export()`` and ``Dataset.load()`` already check
    for the method they need and raise ``UnsupportedFormat`` otherwise, and the
    guard only exposes the methods the plugin really implements.

``extensions``
    Optional; a string or a sequence of strings; normalised to a lowercase
    tuple without leading dots.

``detect`` / ``configure``
    Optional. ``detect`` is used by ``detect_format()``; ``configure`` receives
    the per-plugin options table (see below).

A class or an instance is accepted; a class is instantiated with no arguments,
so classmethods (the built-in style) work unchanged. A module may expose
several formats with ``TABLIB_FORMATS`` or ``get_formats()``; otherwise
module-level classes defined in that module that carry a ``title`` are picked
up. Imported helper classes are ignored, so ``from mylib import BaseFormat``
does not accidentally register anything.

.. code-block:: python

    TABLIB_FORMATS = [MarkdownFormat, CsvFootnotes]

Discovery
---------

Two channels, in this order:

1. ``plugins.paths`` from the config file, then ``$TABLIB_PLUGIN_PATH``.
2. the ``tablib.formats`` entry point group of every installed distribution.

Each candidate is ordered by ``[plugins.priority]`` and then by label; the
registration order is the auto-detection order, so priority has a visible
effect (see `Interaction with built-in formats`_).

Discovery itself imports nothing. Each candidate carries a loader callable, so
an import failure is attributed to the exact file or distribution that caused
it. Directory plugins are imported under a private, collision-free module name
(``tablib_plugin_<stem>_<path-hash>``) and the same path is never imported
twice, even if it appears in both ``paths`` and ``$TABLIB_PLUGIN_PATH``.

Configuration
-------------

Resolution order - the first file that exists wins:

1. the path passed to ``tablib.plugins.load_settings(path=...)``;
2. ``$TABLIB_PLUGIN_CONFIG``;
3. ``%APPDATA%\\tablib\\plugins.toml`` (Windows);
4. ``$XDG_CONFIG_HOME/tablib/plugins.toml``;
5. ``~/.config/tablib/plugins.toml``;
6. ``./tablib-plugins.toml``.

A ``.json`` file is accepted too. TOML needs Python 3.11+ or the ``tomli``
package; without either, JSON keeps working and a TOML file produces a
diagnostic.

File schema
~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 12 68

   * - Key
     - Default
     - Meaning
   * - ``enabled``
     - ``true``
     - ``false`` turns the subsystem into a no-op.
   * - ``entry_points``
     - ``true``
     - Set to ``false`` to ignore installed distributions.
   * - ``paths``
     - ``[]``
     - Extra search paths. Relative paths resolve against the config file, not
       the working directory.
   * - ``disabled``
     - ``[]``
     - Format keys or entry point labels that must not be registered. The plugin
       is still imported (its key is only known after import) but never
       installed.
   * - ``priority``
     - ``{}``
     - ``{label = number}``; lower runs first, so it wins auto-detection.
   * - ``options``
     - ``{}``
     - ``[plugins.options.<key>]`` tables handed to the plugin's
       ``configure()``.
   * - ``max_failures``
     - ``3``
     - Failures before the circuit breaker quarantines a plugin.
   * - ``strict``
     - ``false``
     - When true, a ``detect()`` failure raises instead of counting as
       "no match".
   * - ``allow_override``
     - ``false``
     - When true, a plugin may replace a registered format (see below).

Unknown keys and wrong types are reported as diagnostics, never as import
errors; ``parse_config()`` raises ``PluginConfigError`` for callers that want a
hard failure.

Example::

    [plugins]
    paths = ["./tablib-plugins"]
    disabled = ["ndjson"]
    max_failures = 2
    priority = { markdown = 10 }

    [plugins.options.markdown]
    align = "center"

Environment variables
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Variable
     - Effect
   * - ``TABLIB_PLUGIN_CONFIG``
     - Config file path.
   * - ``TABLIB_PLUGIN_PATH``
     - Extra search paths, ``os.pathsep``-separated.
   * - ``TABLIB_PLUGINS_ENABLED``
     - ``0``/``false``/``off`` disables the subsystem.
   * - ``TABLIB_PLUGINS_ENTRY_POINTS``
     - ``0`` ignores installed distributions.
   * - ``TABLIB_PLUGINS_DISABLE``
     - Comma-separated keys to disable.
   * - ``TABLIB_PLUGINS_STRICT``
     - ``1`` enables strict probe mode.
   * - ``TABLIB_PLUGINS_MAX_FAILURES``
     - Positive integer; overrides the file.

Environment variables are applied after the file, so a deployment can override
a checked-in config without editing it.

Error isolation
---------------

Every phase has its own containment rule:

``config``
    A malformed file falls back to defaults and records
    ``PluginConfigError``. ``import tablib`` never fails because of a config
    typo.

``import``
    Any exception, including ``SystemExit``, is caught and recorded with the
    traceback. ``KeyboardInterrupt`` is re-raised - swallowing it would make
    Ctrl-C unreliable.

``validation``
    A plugin that does not satisfy the contract is rejected with a message
    naming the violated rule; other plugins keep loading.

``registration``
    A key that is already registered is refused unless ``allow_override`` is
    true. A key that collides with an existing ``Dataset`` attribute (``export``,
    ``filter``, ``height``, ...) is refused *even with* ``allow_override`` -
    a plugin must not be able to shadow the host's own API.

``execution``
    Calls go through a guard. A raising ``export_set``/``import_set`` becomes a
    ``PluginExecutionError`` that names the plugin, the method and the original
    error; the failure counter increases; at ``max_failures`` the format is
    quarantined - unregistered, its ``Dataset`` attributes removed, and later
    calls raise ``PluginQuarantined`` (a subclass of ``UnsupportedFormat``).

Two deliberate asymmetries:

* ``detect()`` failures return ``False`` instead of raising, because a probe
  that explodes means "not this format", and aborting a whole import because
  one installed plugin has a bug would be worse. The failure is still recorded
  and counted, and three failing probes quarantine the plugin. Set ``strict``
  if you prefer the error.
* An overriding plugin that gets quarantined restores the built-in format it
  replaced, instead of leaving the key missing. Without this, a broken
  override would look like data loss.

Inspecting the result
---------------------

.. code-block:: python

    >>> import tablib
    >>> tablib.plugins.records()          # one entry per discovered format
    >>> tablib.plugins.diagnostics()      # what failed, with tracebacks
    >>> tablib.plugins.doctor()["ok"]     # health flag for deployments

Command line::

    python -m tablib.plugins list      [--json]
    python -m tablib.plugins doctor    [--json]   # exit 1 when degraded
    python -m tablib.plugins paths     [--json]
    python -m tablib.plugins config    [--json]

``doctor`` prints the effective configuration, the discovered plugins and every
diagnostic, and exits non-zero if anything was rejected or quarantined - which
makes it usable as a deployment health check.

Interaction with built-in formats
---------------------------------

* Built-in formats are registered first and probed first by
  ``detect_format()``, so installing a plugin cannot change how ``csv`` or
  ``json`` inputs are detected. Plugin priority only orders plugins among
  themselves.
* This is why, for example, comma-separated NDJSON is still detected as ``csv``
  by the built-in sniffer: pass ``format="ndjson"`` explicitly, or set
  ``allow_override`` and accept the consequences.
* ``allow_override = true`` replaces a built-in format for the whole process.
  Use it for a deliberately better implementation of an existing format, not to
  add a new one.

Performance
-----------

With the subsystem enabled the only cost on the hot path is one boolean check
per ``Registry.get_format()`` call (the load pass runs once). The plugin module
tree is imported lazily: ``import tablib`` does not import
``importlib.metadata``, ``tomllib`` or any plugin file until a format is looked
up.

Security and limitations
------------------------

What is defended: key squatting, silent overwrites, host API shadowing,
import-time exceptions, ``SystemExit``, exceptions raised by plugin methods, and
state corruption after a failure.

What is **not** defended - be explicit about this before installing a plugin you
do not trust:

* plugins execute arbitrary Python in the host process, with the host's
  privileges. There is no sandbox;
* a plugin that blocks (infinite loop, blocking I/O) cannot be interrupted;
  Python threads cannot be killed, so a wall-clock timeout would be a lie;
* ``os._exit()``, a segfault in a C extension, or memory exhaustion take the
  process down;
* the circuit breaker bounds *consecutive* damage, not the first call: a plugin
  can still corrupt the ``Dataset`` it was handed before raising.

For untrusted code the only sound answer is process isolation: run the import
in a subprocess, or convert data in a worker and pass files across the boundary.
The plugin API is a packaging and robustness feature, not a security boundary.

Design notes
------------

* The registry is the only seam. ``core.py`` is untouched, which is what makes
  the differential check cheap and convincing: the plugin subsystem hooks
  ``Registry.get_format()`` and ``Registry.formats()`` and nothing else.
* ``Registry.formats()`` snapshots its keys because a plugin can now be
  quarantined during its own ``detect()`` call - mutating the dict mid-iteration
  would raise ``RuntimeError: dictionary changed size during iteration``.
* ``Registry.register()`` grew ``replace=False``, ``unregister()``, ``has()``,
  ``keys()`` and ``peek()``. The default of ``register()`` is still "overwrite",
  so existing callers and tests keep their behaviour.

See also
--------

* ``examples/tablib-markdown-format`` - import + export + detect + option.
* ``examples/tablib-ndjson-format`` - a format that deliberately loses
  auto-detection against a greedy built-in.
* ``tools/diff_equivalence.py`` - upstream vs. patched comparison.
* ``tools/mutation_check.py`` - negative control for the plugin tests.
