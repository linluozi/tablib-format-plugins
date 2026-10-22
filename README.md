# Tablib: format-agnostic tabular dataset library

[![Jazzband](https://jazzband.co/static/img/badge.svg)](https://jazzband.co/)
[![PyPI version](https://img.shields.io/pypi/v/tablib.svg)](https://pypi.org/project/tablib/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/tablib.svg)](https://pypi.org/project/tablib/)
[![PyPI downloads](https://img.shields.io/pypi/dm/tablib.svg)](https://pypistats.org/packages/tablib)
[![GitHub Actions status](https://github.com/jazzband/tablib/workflows/Test/badge.svg)](https://github.com/jazzband/tablib/actions)
[![codecov](https://codecov.io/gh/jazzband/tablib/branch/master/graph/badge.svg)](https://codecov.io/gh/jazzband/tablib)
[![GitHub](https://img.shields.io/github/license/jazzband/tablib.svg)](LICENSE)

    _____         ______  ___________ ______
    __  /_______ ____  /_ ___  /___(_)___  /_
    _  __/_  __ `/__  __ \__  / __  / __  __ \
    / /_  / /_/ / _  /_/ /_  /  _  /  _  /_/ /
    \__/  \__,_/  /_.___/ /_/   /_/   /_.___/


Tablib is a format-agnostic tabular dataset library, written in Python.

Output formats supported:

- Excel (Sets + Books)
- JSON (Sets + Books)
- YAML (Sets + Books)
- Pandas DataFrames (Sets)
- HTML (Sets)
- Jira (Sets)
- LaTeX (Sets)
- TSV (Sets)
- ODS (Sets)
- CSV (Sets)
- DBF (Sets)
- SQL (Sets)

Note that tablib *purposefully* excludes XML support. It always will. (Note: This is a
joke. Pull requests are welcome.)

## Format plugins

Formats can be added from outside the package - no fork, no monkeypatching.

```bash
pip install tablib-markdown-format       # any distribution declaring the entry point
```

```python
import tablib

data = tablib.Dataset(headers=["name", "qty"])
data.append(["apple", 3])
print(data.export("markdown"))
```

Plugins are discovered from the `tablib.formats` entry point group and from
directories listed in a config file (`~/.config/tablib/plugins.toml` or
`./tablib-plugins.toml`):

```toml
[plugins]
paths = ["./tablib-plugins"]
disabled = ["unwanted"]
max_failures = 3

[plugins.options.markdown]
align = "center"
```

A plugin that fails to import, breaks the format contract, tries to steal a
built-in key or raises while exporting is isolated: the error is recorded, the
plugin is quarantined after repeated failures, and the rest of tablib keeps
working. Inspect the result with:

```bash
python -m tablib.plugins doctor
```

See [docs/plugins.rst](docs/plugins.rst) for the contract, the configuration
reference and the isolation model, and `examples/` for two complete plugins.


Tablib documentation is graciously hosted on https://tablib.readthedocs.io

It is also available in the ``docs`` directory of the source distribution.

Make sure to check out [Tablib on PyPI](https://pypi.org/project/tablib/)!

## Contribute

Please see the [contributing guide](https://github.com/jazzband/tablib/blob/master/.github/CONTRIBUTING.md).
