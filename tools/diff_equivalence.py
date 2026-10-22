"""Differential equivalence check: upstream tablib vs. the plugin-enabled tree.

The plugin subsystem is only acceptable if a program that installs no plugin
behaves exactly like upstream.  This script runs the same probe against two
source trees in two separate interpreters and compares:

* the registry keys and their order (order drives auto-detection);
* every built-in export, byte hash for text formats and round-trip identity for
  binary ones (xlsx/xls/ods/dbf embed timestamps, so bytes are not comparable);
* auto-detection on a corpus of realistic and hostile inputs;
* the public attribute surface of ``Dataset`` (plugins must not add any).

Usage::

    python tools/diff_equivalence.py ../upstream/src ./src
"""

import json
import os
import subprocess
import sys
import textwrap

PROBE = r'''
import hashlib
import json
import sys

import tablib
from tablib.formats import registry

samples = {
    "csv": "name,qty\r\napple,3\r\npear|plum,5\r\n",
    "tsv": "name\tqty\r\napple\t3\r\n",
    "json": '[{"name": "apple", "qty": 3}]',
    "yaml": "- {name: apple, qty: 3}\n",
    "html": (
        "<table><thead><tr><th>name</th></tr></thead>"
        "<tbody><tr><td>a</td></tr></tbody></table>"
    ),
    "rst": "====  ===\nname  qty\n====  ===\na      1\n====  ===",
    "latex": "\\begin{tabular}{lr}\nname & qty \\\\\n\\end{tabular}\n",
}

data = tablib.Dataset(headers=["name", "qty"])
data.append(["apple", 3])
data.append(["pear|plum", 5])

report = {"keys": list(registry._formats), "exports": {}, "detect": {}}
report["dataset_attrs"] = sorted(
    name for name in vars(tablib.Dataset) if not name.startswith("_")
)

for key in list(registry._formats):
    try:
        value = data.export(key)
    except Exception as err:
        report["exports"][key] = {"error": type(err).__name__}
        continue
    if isinstance(value, str):
        report["exports"][key] = {
            "kind": "text",
            "len": len(value),
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }
    elif isinstance(value, (bytes, bytearray)):
        try:
            restored = tablib.Dataset().load(bytes(value), format=key)
            report["exports"][key] = {
                "kind": "binary",
                "roundtrip": [list(row) for row in restored._package(dicts=False)],
            }
        except Exception as err:
            report["exports"][key] = {"kind": "binary", "roundtrip_error": type(err).__name__}
    else:
        text = str(value)
        report["exports"][key] = {
            "kind": "object",
            "type": type(value).__name__,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

corpus = list(samples.values()) + [
    '{"a": 1}\n{"a": 2}',
    "| a | b |\n| --- | --- |\n| 1 | 2 |",
    '¡¡¡¡¡¡---///\n\n\n¡¡£™∞¢£§∞',
    "",
    "   ",
    "\x00\x01\x02",
]
for index, sample in enumerate(corpus):
    try:
        report["detect"][str(index)] = tablib.detect_format(sample)
    except Exception as err:
        report["detect"][str(index)] = "ERROR:" + type(err).__name__

for index, sample in enumerate(corpus):
    if isinstance(sample, str) and "," in sample:
        try:
            restored = tablib.import_set(sample)
            report.setdefault("import", {})[str(index)] = [
                list(row) for row in restored._package(dicts=False)
            ]
        except Exception as err:
            report.setdefault("import", {})[str(index)] = "ERROR:" + type(err).__name__

json.dump(report, sys.stdout)
'''


def probe(src, disabled=True):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath(src)
    if disabled:
        env["TABLIB_PLUGINS_ENABLED"] = "0"
    else:
        # Subsystem on, but nothing reachable: the comparison must not depend on
        # whatever plugin happens to be installed in the current interpreter.
        env.pop("TABLIB_PLUGINS_ENABLED", None)
        env["TABLIB_PLUGINS_ENTRY_POINTS"] = "0"
    for name in ("TABLIB_PLUGIN_CONFIG", "TABLIB_PLUGIN_PATH"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROBE)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    if result.returncode != 0:
        raise SystemExit(f"probe failed for {src}:\n{result.stderr}")
    return json.loads(result.stdout)


def compare(name, left, right, problems):
    if left == right:
        print(f"  ok   {name}")
        return
    problems.append(name)
    print(f"  FAIL {name}")
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                print(f"        {key}: upstream={left.get(key)!r} patched={right.get(key)!r}")
    else:
        print(f"        upstream={left!r}")
        print(f"        patched ={right!r}")


def main(argv):
    upstream_src = argv[1] if len(argv) > 1 else "../upstream/src"
    patched_src = argv[2] if len(argv) > 2 else "src"

    problems = []
    print("probing upstream ...")
    upstream = probe(upstream_src)

    for disabled in (True, False):
        label = (
            "plugins disabled" if disabled
            else "subsystem on, no plugin reachable"
        )
        print(f"\nprobing patched tree ({label}) ...")
        patched = probe(patched_src, disabled=disabled)
        print(f"comparison [{label}]:")
        compare("registry keys and order", upstream["keys"], patched["keys"], problems)
        compare("Dataset public attributes", upstream["dataset_attrs"],
                patched["dataset_attrs"], problems)
        compare("exports", upstream["exports"], patched["exports"], problems)
        compare("auto-detection", upstream["detect"], patched["detect"], problems)
        compare("auto-import", upstream.get("import"), patched.get("import"), problems)

    print()
    if problems:
        print(f"FAILED - {len(problems)} difference(s): {', '.join(problems)}")
        return 1
    print("ALL EQUIVALENT - zero-plugin behaviour matches upstream in both modes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
