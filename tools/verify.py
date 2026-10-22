"""Acceptance checks for the tablib plugin system.

Run from a fresh checkout or an extracted archive::

    python tools/verify.py                # checks + test suites
    python tools/verify.py --skip-pytest  # checks only

Every check is a concrete assertion (exact strings, exact exception types,
exact registry keys); there is no "import succeeded, therefore fine".
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Upstream test that fails on any machine east of UTC: the vendored DBF helper
# converts a timestamp with local time and the test hard-codes a UTC date.
KNOWN_FAILURES = {
    "tests/test_tablib_dbfpy_packages_utils.py::UtilsGetDateTestCase"
    "::test_getDate_datetime_timestamp",
}

results = []


def check(name, func):
    try:
        detail = func()
    except Exception as err:  # noqa: BLE001 - a check failure must not stop the run
        results.append((name, False, f"{type(err).__name__}: {err}"))
        print(f"  FAIL {name}: {type(err).__name__}: {err}")
        return False
    results.append((name, True, detail or ""))
    print(f"  ok   {name}" + (f"  ({detail})" if detail else ""))
    return True


def write_plugin(directory, name, source):
    path = Path(directory) / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def dataset():
    import tablib

    data = tablib.Dataset(headers=["name", "qty"])
    data.append(["apple", 3])
    data.append(["pear|plum", 5])
    return data


def checks():
    import tablib

    print(f"tablib imported from: {tablib.__file__}")
    print(f"version: {tablib.__version__}")
    print()

    print("built-in behaviour:")

    def keys():
        from tablib.formats import registry

        keys = list(registry.keys())
        assert len(keys) == 15, keys
        assert keys[:3] == ["json", "xlsx", "xls"], keys
        return f"{len(keys)} formats"

    def exports():
        data = dataset()
        assert data.export("csv") == "name,qty\r\napple,3\r\npear|plum,5\r\n"
        assert data.export("tsv") == "name\tqty\r\napple\t3\r\npear|plum\t5\r\n"
        assert data.export("json") == (
            '[{"name": "apple", "qty": 3}, {"name": "pear|plum", "qty": 5}]'
        )
        assert data.export("html") == (
            "<table><thead><tr><th>name</th><th>qty</th></tr></thead><tbody>"
            "<tr><td>apple</td><td>3</td></tr><tr><td>pear|plum</td><td>5</td></tr>"
            "</tbody></table>"
        )
        assert data.export("sql") == (
            "INSERT INTO export_table (name,qty) VALUES ('apple', 3);\n"
            "INSERT INTO export_table (name,qty) VALUES ('pear|plum', 5);\n"
        )
        return "5 text formats byte-identical"

    def detection():
        assert tablib.detect_format("a,b\n1,2\n") == "csv"
        assert tablib.detect_format('[{"a": 1}]') == "json"
        assert tablib.detect_format("a\tb\n1\t2") == "tsv"
        assert tablib.detect_format("\x00\x01\x02") is None
        return "4 probes"

    def unknown_format():
        from tablib.exceptions import UnsupportedFormat

        try:
            dataset().export("nope")
        except UnsupportedFormat as err:
            assert str(err) == "Tablib has no format 'nope' or it is not registered.", str(err)
            return "message unchanged"
        raise AssertionError("export of an unknown format did not raise")

    check("registry keys and order", keys)
    check("built-in exports", exports)
    check("auto-detection", detection)
    check("unknown format message", unknown_format)

    print()
    print("discovery, loading, configuration:")

    tmp = Path(tempfile.mkdtemp(prefix="tablib-verify-"))
    try:
        plugin_dir = tmp / "plugins"
        write_plugin(plugin_dir, "pipe", '''
            class PipeFormat:
                title = "pipe"
                extensions = ("pipe",)
                prefix = ""

                def configure(self, options):
                    self.prefix = options.get("prefix", "")

                def export_set(self, dataset):
                    return self.prefix + "|".join(
                        str(cell) for row in dataset._package(dicts=False) for cell in row
                    )

                def import_set(self, dset, in_stream):
                    dset.wipe()
                    dset.append(in_stream.getvalue().split("|"))

                def detect(self, stream):
                    return stream.read(16).startswith("PIPE|")
            ''')
        config = tmp / "tablib-plugins.toml"
        config.write_text(textwrap.dedent('''
            [plugins]
            paths = ["plugins"]

            [plugins.options.pipe]
            prefix = "PIPE|"
            '''), encoding="utf-8")

        def discovery():
            os.environ["TABLIB_PLUGIN_CONFIG"] = str(config)
            os.environ.pop("TABLIB_PLUGINS_ENABLED", None)
            tablib.plugins.reset()
            out = dataset().export("pipe")
            assert out.startswith("PIPE|name|qty"), out
            record = [r for r in tablib.plugins.records() if r.key == "pipe"][0]
            assert record.state.value == "active"
            assert record.source.value == "directory"
            assert record.options == {"prefix": "PIPE|"}
            return "lazy load + configure()"

        def detection_order():
            assert tablib.detect_format("PIPE|a|b") == "pipe"
            assert tablib.detect_format("a,b\n1,2\n") == "csv"
            return "plugin after built-ins"

        def disabling():
            os.environ["TABLIB_PLUGINS_DISABLE"] = "pipe"
            tablib.plugins.reset()
            try:
                dataset().export("pipe")
            except tablib.UnsupportedFormat:
                pass
            else:
                raise AssertionError("disabled plugin still exported")
            assert dataset().export("csv").startswith("name,qty")
            os.environ.pop("TABLIB_PLUGINS_DISABLE", None)
            return "env disable honoured"

        def empty_state():
            os.environ["TABLIB_PLUGINS_ENABLED"] = "0"
            tablib.plugins.reset()
            assert tablib.plugins.doctor()["ok"] is True
            assert tablib.plugins.registered_keys() == []
            os.environ.pop("TABLIB_PLUGINS_ENABLED", None)
            return "subsystem can be switched off"

        check("directory discovery + options", discovery)
        check("detection order", detection_order)
        check("configuration disables a format", disabling)
        check("kill switch", empty_state)

        print()
        print("error isolation:")

        bad_dir = tmp / "bad"
        write_plugin(bad_dir, "explodes", 'raise RuntimeError("boom at import")\n')
        write_plugin(bad_dir, "runtime", '''
            class Runtime:
                title = "runtime"

                def export_set(self, dataset):
                    raise ValueError("boom at export")
            ''')
        write_plugin(bad_dir, "thief", '''
            class Thief:
                title = "csv"

                def export_set(self, dataset):
                    return "HIJACKED"
            ''')

        def import_isolation():
            os.environ["TABLIB_PLUGIN_PATH"] = str(bad_dir)
            os.environ["TABLIB_PLUGINS_MAX_FAILURES"] = "2"
            tablib.plugins.reset()
            assert dataset().export("csv").startswith("name,qty"), "built-in csv was replaced"
            phases = {d.plugin: d.phase.value for d in tablib.plugins.diagnostics()}
            assert phases.get("explodes") == "import", phases
            assert phases.get("thief") == "registration", phases
            return "import error + key theft rejected"

        def runtime_isolation():
            from tablib.exceptions import PluginExecutionError, PluginQuarantined

            try:
                dataset().export("runtime")
            except PluginExecutionError as err:
                assert "'runtime'" in str(err), str(err)
            else:
                raise AssertionError("plugin error was not reported")

            assert dataset().export("json").startswith('[{"name"')
            try:
                dataset().export("runtime")
            except PluginExecutionError as err:
                assert "quarantined" in str(err), str(err)
            else:
                raise AssertionError("second failure did not raise")

            try:
                dataset().export("runtime")
            except PluginQuarantined:
                pass
            else:
                raise AssertionError("quarantined format still exportable")

            from tablib import Dataset

            assert not hasattr(Dataset, "runtime")
            assert tablib.plugins.quarantine_reason("runtime") is not None
            return "2 failures -> quarantine"

        def reserved_names():
            # A plugin titled "export" must be refused even with allow_override.
            write_plugin(tmp / "shadow", "shadow", '''
                class Shadow:
                    title = "export"

                    def export_set(self, dataset):
                        return "shadowed"
                ''')
            cfg = tmp / "override.toml"
            cfg.write_text(textwrap.dedent('''
                [plugins]
                paths = ["shadow"]
                allow_override = true
                '''), encoding="utf-8")
            os.environ.pop("TABLIB_PLUGIN_PATH", None)
            os.environ["TABLIB_PLUGIN_CONFIG"] = str(cfg)
            tablib.plugins.reset()
            assert dataset().export("csv").startswith("name,qty")
            states = {r.key: r.state.value for r in tablib.plugins.records()}
            assert states.get("export") == "rejected", states
            return "Dataset.export cannot be shadowed"

        check("import-time failure contained", import_isolation)
        check("runtime failure + circuit breaker", runtime_isolation)
        check("reserved attribute refused", reserved_names)

        print()
        print("CLI:")

        def cli():
            env = dict(os.environ)
            env["TABLIB_PLUGINS_ENABLED"] = "0"
            env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [sys.executable, "-m", "tablib.plugins", "doctor", "--json"],
                capture_output=True, text=True, encoding="utf-8", env=env,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            payload = json.loads(result.stdout)
            assert payload["ok"] is True, payload
            assert len(payload["builtin_formats"]) == 15, payload["builtin_formats"]
            return "doctor --json exit 0"

        check("python -m tablib.plugins doctor", cli)
    finally:
        for name in (
            "TABLIB_PLUGIN_CONFIG", "TABLIB_PLUGIN_PATH", "TABLIB_PLUGINS_DISABLE",
            "TABLIB_PLUGINS_ENABLED", "TABLIB_PLUGINS_MAX_FAILURES", "TABLIB_ALLOW",
        ):
            os.environ.pop(name, None)
        tablib.plugins.reset()
        shutil.rmtree(tmp, ignore_errors=True)


def run_pytest(target, allow_known_failures=False):
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--no-cov", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = result.stdout + result.stderr
    summary = [line for line in output.splitlines() if " passed" in line or " failed" in line]
    failed = [
        line.split(" ", 1)[1].strip()
        for line in output.splitlines()
        if line.startswith("FAILED ")
    ]
    allowed = {node for node in failed if node.split(" ")[0] in KNOWN_FAILURES}
    unexpected = [node for node in failed if node not in allowed]
    ok = result.returncode == 0 or (allow_known_failures and not unexpected)
    detail = summary[-1] if summary else "no summary"
    if allowed:
        detail += f", {len(allowed)} known upstream failure(s)"
    return ok, detail, unexpected


def main():
    parser = argparse.ArgumentParser(description="Verify the tablib plugin system.")
    parser.add_argument("--skip-pytest", action="store_true", help="skip the test suites")
    args = parser.parse_args()

    checks()

    if not args.skip_pytest:
        print()
        print("test suites:")
        ok, detail, unexpected = run_pytest("tests", allow_known_failures=True)
        print(f"  {'ok  ' if ok else 'FAIL'} pytest tests  ({detail})")
        results.append(("pytest tests", ok, detail))
        if unexpected:
            for node in unexpected:
                print(f"        unexpected failure: {node}")

        examples = sorted(str(path.parent.parent.parent) for path in
                          (REPO / "examples").glob("*/tests/test_*.py"))
        if examples:
            try:
                import tablib_markdown_format  # noqa: F401
                import tablib_ndjson_format  # noqa: F401
            except ImportError as err:
                print(f"  skip examples (not installed: {err})")
            else:
                ok, detail, _ = run_pytest("examples")
                print(f"  {'ok  ' if ok else 'FAIL'} pytest examples  ({detail})")
                results.append(("pytest examples", ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print()
    if passed != total:
        print(f"{passed}/{total} CHECKS PASSED - FAILURES:")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    print(f"{passed}/{total} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
