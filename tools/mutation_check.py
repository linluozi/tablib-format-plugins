"""Negative control: do the plugin tests actually detect a broken implementation?

For every mutation below the script copies ``src`` to a temporary tree, breaks
exactly one behaviour, and runs the test that is supposed to catch it.  A
mutation that survives means the corresponding test proves nothing.

Usage::

    python tools/mutation_check.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

MUTATIONS = [
    (
        "import isolation removed",
        "src/tablib/plugins/manager.py",
        """        except KeyboardInterrupt:
            raise
        except BaseException as exc:
            self._diagnose(
                candidate.label, Phase.IMPORT, exc, origin=candidate.origin,""",
        """        except KeyboardInterrupt:
            raise
        except ValueError as exc:
            self._diagnose(
                candidate.label, Phase.IMPORT, exc, origin=candidate.origin,""",
        "tests/test_plugins.py::TestErrorIsolation::test_import_error_is_contained",
    ),
    (
        "conflict detection disabled",
        "src/tablib/plugins/manager.py",
        """        if not registry.has(key):""",
        """        if True:""",
        "tests/test_plugins.py::TestConflicts::test_plugin_cannot_replace_builtin_by_default",
    ),
    (
        "circuit breaker disabled",
        "src/tablib/plugins/manager.py",
        """            if record.state is State.ACTIVE and record.failures""",
        """            if record.state is State.ACTIVE and False and record.failures""",
        (
            "tests/test_plugins.py::TestErrorIsolation"
            "::test_quarantine_after_threshold_removes_format"
        ),
    ),
    (
        "detect failures propagate",
        "src/tablib/plugins/guard.py",
        """            if name in PROBE_METHODS and not self._manager.strict:
                return False""",
        """            if False:
                return False""",
        "tests/test_plugins.py::TestErrorIsolation::test_detect_error_counts_as_no_match",
    ),
    (
        "config schema not validated",
        "src/tablib/plugins/config.py",
        """    unknown = sorted(set(table) - _TOP_LEVEL_KEYS)""",
        """    unknown = []""",
        "tests/test_plugins.py::TestConfiguration::test_parse_config_rejects_unknown_key",
    ),
    (
        "reserved attribute check removed",
        "src/tablib/plugins/manager.py",
        """            for name in (key, f"get_{key}", f"set_{key}"):
                if hasattr(Dataset, name) or hasattr(Databook, name):""",
        """            for name in ():
                if hasattr(Dataset, name) or hasattr(Databook, name):""",
        (
            "tests/test_plugins.py::TestConflicts"
            "::test_reserved_dataset_attribute_is_refused_even_with_override"
        ),
    ),
]


def run_pytest(node, pythonpath):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(pythonpath)
    env["TABLIB_PLUGINS_ENTRY_POINTS"] = "0"
    for name in ("TABLIB_PLUGIN_CONFIG", "TABLIB_PLUGIN_PATH", "TABLIB_PLUGINS_ENABLED"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", node, "-q", "--no-cov", "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8",
    )
    return result.returncode, result.stdout + result.stderr


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_src = Path(tmp) / "src"
        shutil.copytree(REPO / "src", tmp_src)

        for name, relative, old, new, node in MUTATIONS:
            target = Path(tmp) / relative
            original = target.read_text(encoding="utf-8")
            if original.count(old) != 1:
                print(f"  SKIP {name}: mutation anchor found {original.count(old)} times")
                failures.append(name)
                continue
            target.write_text(original.replace(old, new), encoding="utf-8")
            code, output = run_pytest(node, tmp_src)
            target.write_text(original, encoding="utf-8")

            if code == 0:
                print(f"  SURVIVED {name}: tests still pass with the behaviour removed")
                failures.append(name)
            else:
                last = [
                    line for line in output.splitlines()
                    if line.startswith("FAILED") or " failed" in line
                ]
                print(f"  killed   {name}  ({last[-1] if last else 'pytest failed'})")

    print()
    if failures:
        print(f"FAILED - {len(failures)} mutation(s) survived: {', '.join(failures)}")
        return 1
    print(f"ALL {len(MUTATIONS)} MUTATIONS KILLED - the tests have discriminating power")
    return 0


if __name__ == "__main__":
    sys.exit(main())
