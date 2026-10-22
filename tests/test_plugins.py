"""Tests for the tablib plugin subsystem.

The tests are deliberately hostile: plugins that explode at import time, at
registration time and at call time, plugins that try to steal a built-in key,
plugins that would shadow ``Dataset.export``, malformed config files.  A plugin
system that only works with well-behaved plugins is not a plugin system.
"""

import json
import os
import subprocess
import sys
import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest

import tablib
import tablib.plugins as plugins
from tablib import Dataset
from tablib.exceptions import (
    InvalidFormatPlugin,
    PluginConfigError,
    PluginExecutionError,
    PluginQuarantined,
    UnsupportedFormat,
)
from tablib.formats import registry
from tablib.plugins import discovery
from tablib.plugins.config import load_settings, parse_config
from tablib.plugins.protocol import extract_formats, validate_format

ENVIRONMENT_VARIABLES = (
    "TABLIB_PLUGIN_CONFIG",
    "TABLIB_PLUGIN_PATH",
    "TABLIB_PLUGINS_DISABLE",
    "TABLIB_PLUGINS_ENABLED",
    "TABLIB_PLUGINS_ENTRY_POINTS",
    "TABLIB_PLUGINS_STRICT",
    "TABLIB_PLUGINS_MAX_FAILURES",
)

BUNK = (
    '¡¡¡¡¡¡---///\n\n\n'
    '¡¡£™∞¢£§∞§¶•¶ª∞¶•ªº••ª–º§•†•§º¶•†¥ª–º•§ƒø¥¨©πƒø†ˆ¥ç©¨√øˆ¥≈†ƒ¥ç©ø¨çˆ¥ƒçø¶'
)


@pytest.fixture(autouse=True)
def plugin_isolation(request, monkeypatch):
    """Give every test a pristine registry and a plugin-free environment."""
    snapshot = dict(registry._formats)
    plugins.reset()
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    if "real_entry_points" not in request.keywords:
        # Installed example plugins must not leak into unit tests.
        monkeypatch.setattr(discovery, "_entry_points", list)
    yield
    plugins.reset()
    for key in [key for key in registry.keys() if key not in snapshot]:
        registry.unregister(key)
    for key, value in snapshot.items():
        if not registry.has(key):
            registry.register(key, value)


def _dataset():
    data = Dataset(headers=['name', 'qty'])
    data.append(['apple', 3])
    data.append(['pear|plum', 5])
    return data


def _write(directory, filename, source):
    path = Path(directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _format_source(title, payload=None, marker=None, cls="Fmt", extra=""):
    return f'''
        class {cls}:
            title = "{title}"
            extensions = ("{title}",)
            payload = "{payload or title + '-payload'}"
            marker = "{marker or '|' + title + '|'}"

            def export_set(self, dataset):
                return self.payload

            def import_set(self, dset, in_stream):
                dset.wipe()
                dset.append([in_stream.getvalue().strip()])

            def detect(self, stream):
                return stream.read(64).startswith(self.marker)
        {extra}
        '''


def _records_by_key():
    records = {}
    for record in plugins.records():
        records.setdefault(record.key, record)
    return records


# ----------------------------------------------------------------------
# discovery
# ----------------------------------------------------------------------

class TestDiscovery:
    def test_directory_file_is_discovered(self, tmp_path, monkeypatch):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("pipe") == "pipe-payload"

        record = _records_by_key()["pipe"]
        assert record.state is plugins.State.ACTIVE
        assert record.source is plugins.Source.DIRECTORY
        assert record.failures == 0
        assert plugins.get_manager().discovered == 1

    def test_directory_package_is_discovered(self, tmp_path, monkeypatch):
        _write(tmp_path / "pkgplug", "__init__.py", _format_source("pkgfmt"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("pkgfmt") == "pkgfmt-payload"
        assert _records_by_key()["pkgfmt"].label == "pkgplug"

    def test_single_file_search_path(self, tmp_path, monkeypatch):
        path = _write(tmp_path, "only.py", _format_source("onlyfmt"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(path))

        assert _dataset().export("onlyfmt") == "onlyfmt-payload"

    def test_entry_point_is_discovered(self, tmp_path, monkeypatch):
        _write(tmp_path, "epmod.py", '''
            class EntryFormat:
                title = "entryfmt"

                def export_set(self, dataset):
                    return "from-entry-point"

                def import_set(self, dset, in_stream):
                    dset.append([in_stream.getvalue()])
            ''')
        monkeypatch.syspath_prepend(str(tmp_path))
        entry_point = EntryPoint(
            name="entryfmt", value="epmod:EntryFormat", group="tablib.formats"
        )
        monkeypatch.setattr(discovery, "_entry_points", lambda: [entry_point])

        assert _dataset().export("entryfmt") == "from-entry-point"
        record = _records_by_key()["entryfmt"]
        assert record.source is plugins.Source.ENTRY_POINT
        assert "epmod:EntryFormat" in record.origin

    def test_missing_search_path_is_reported(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "ok.py", _format_source("okfmt"))
        missing = tmp_path / "nope"
        monkeypatch.setenv(
            "TABLIB_PLUGIN_PATH", os.pathsep.join([str(missing), str(tmp_path / "plugins")])
        )

        assert _dataset().export("okfmt") == "okfmt-payload"
        errors = [item for item in plugins.diagnostics() if item.error_type == "PathNotFound"]
        assert len(errors) == 1
        assert str(missing) in errors[0].message

    def test_repeated_load_does_not_duplicate(self, tmp_path, monkeypatch):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        plugins.load()
        plugins.load()
        assert len(plugins.records()) == 1

        plugins.load(force=True)
        assert len(plugins.records()) == 1
        assert _dataset().export("pipe") == "pipe-payload"

    def test_unknown_format_triggers_lazy_load(self, tmp_path, monkeypatch):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        # No explicit load(): Dataset.export() must find the plugin by itself.
        assert _dataset().export("pipe") == "pipe-payload"
        assert plugins.registered_keys() == ["pipe"]


# ----------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------

class TestConfiguration:
    def test_config_file_disables_format(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "pipe.py", _format_source("pipe"))
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            disabled = ["pipe"]
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        record = _records_by_key()["pipe"]
        assert record.state is plugins.State.DISABLED
        with pytest.raises(UnsupportedFormat) as excinfo:
            _dataset().export("pipe")
        assert "has no format 'pipe'" in str(excinfo.value)
        assert _dataset().export("csv").startswith("name,qty")

    def test_config_selected_through_environment(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "pipe.py", _format_source("pipe"))
        config = _write(tmp_path, "custom.toml", '''
            [plugins]
            paths = ["plugins"]
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert _dataset().export("pipe") == "pipe-payload"
        assert plugins.settings().source == str(config)

    def test_relative_paths_resolve_against_config_file(self, tmp_path, monkeypatch):
        config = _write(tmp_path / "conf", "plugins.toml", '''
            [plugins]
            paths = ["vendored"]
            ''')
        _write(tmp_path / "conf" / "vendored", "vend.py", _format_source("vendfmt"))
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert _dataset().export("vendfmt") == "vendfmt-payload"
        assert plugins.settings().paths == (str(tmp_path / "conf" / "vendored"),)

    def test_options_are_handed_to_configure(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "prefixed.py", '''
            class Prefixed:
                title = "prefixed"
                prefix = ""

                def configure(self, options):
                    if "prefix" not in options:
                        raise ValueError("prefix option is required")
                    self.prefix = options["prefix"]

                def export_set(self, dataset):
                    return self.prefix + "payload"
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]

            [plugins.options.prefixed]
            prefix = ">> "
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert _dataset().export("prefixed") == ">> payload"
        assert _records_by_key()["prefixed"].options == {"prefix": ">> "}

    def test_failing_configure_rejects_plugin(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "needy.py", '''
            class Needy:
                title = "needy"

                def configure(self, options):
                    raise ValueError("no options accepted")

                def export_set(self, dataset):
                    return "unreachable"
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]

            [plugins.options.needy]
            anything = 1
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        record = _records_by_key()["needy"]
        assert record.state is plugins.State.REJECTED
        assert "configuration failed" in record.detail
        phases = [item.phase for item in plugins.diagnostics()]
        assert plugins.Phase.CONFIGURATION in phases
        with pytest.raises(UnsupportedFormat):
            _dataset().export("needy")

    def test_priority_decides_auto_detection_order(self, tmp_path, monkeypatch):
        # 'aaa.py' wins by name, 'zzz.py' wins by configured priority.
        _write(tmp_path / "plugins", "aaa.py",
               _format_source("zedfmt", marker="MAGIC:", payload="from-zed"))
        _write(tmp_path / "plugins", "zzz.py",
               _format_source("alphafmt", marker="MAGIC:", payload="from-alpha"))
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert tablib.detect_format("MAGIC:payload") == "zedfmt"

        config.write_text(textwrap.dedent('''
            [plugins]
            paths = ["plugins"]

            [plugins.priority]
            alphafmt = 10
            '''), encoding="utf-8")
        plugins.load(force=True)

        assert tablib.detect_format("MAGIC:payload") == "alphafmt"

    def test_broken_config_degrades_to_defaults(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "pipe.py", _format_source("pipe"))
        config = _write(tmp_path, "tablib-plugins.toml", "paths = [ this is not toml\n")
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path / "plugins"))

        assert _dataset().export("pipe") == "pipe-payload"
        config_errors = [
            item for item in plugins.diagnostics() if item.plugin == "<config>"
        ]
        assert len(config_errors) == 1
        assert config_errors[0].error_type == "PluginConfigError"

    def test_parse_config_rejects_unknown_key(self):
        with pytest.raises(PluginConfigError) as excinfo:
            parse_config({"plugins": {"max_failur": 2}})
        assert "unknown key" in str(excinfo.value)
        assert "max_failur" in str(excinfo.value)

    def test_parse_config_rejects_bad_types(self):
        with pytest.raises(PluginConfigError):
            parse_config({"plugins": {"max_failures": 0}})
        with pytest.raises(PluginConfigError):
            parse_config({"plugins": {"enabled": "maybe"}})
        with pytest.raises(PluginConfigError):
            parse_config({"plugins": {"paths": [1, 2]}})

    def test_missing_configured_file_is_diagnosed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(tmp_path / "gone.toml"))
        settings, diagnostics = load_settings()
        assert settings.enabled is True
        assert diagnostics[0].error_type == "ConfigNotFound"

    def test_environment_can_disable_the_subsystem(self, tmp_path, monkeypatch):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))
        monkeypatch.setenv("TABLIB_PLUGINS_ENABLED", "0")

        with pytest.raises(UnsupportedFormat):
            _dataset().export("pipe")
        assert plugins.get_manager().discovered == 0
        assert _dataset().export("csv") == "name,qty\r\napple,3\r\npear|plum,5\r\n"

    def test_max_failures_environment_override(self, tmp_path, monkeypatch):
        _write(tmp_path, "boom.py", '''
            class Boom:
                title = "boom"

                def export_set(self, dataset):
                    raise ValueError("kaboom")
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))
        monkeypatch.setenv("TABLIB_PLUGINS_MAX_FAILURES", "1")

        with pytest.raises(PluginExecutionError):
            _dataset().export("boom")
        with pytest.raises(PluginQuarantined):
            _dataset().export("boom")


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------

class TestValidation:
    def test_module_without_format_is_rejected(self, tmp_path, monkeypatch):
        _write(tmp_path, "empty.py", "VALUE = 42\n")
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert any(
            item.phase is plugins.Phase.VALIDATION for item in plugins.diagnostics()
        )
        assert _records_by_key()["empty"].state is plugins.State.REJECTED
        assert _dataset().export("csv").startswith("name,qty")

    def test_missing_title_is_rejected(self, tmp_path, monkeypatch):
        _write(tmp_path, "notitle.py", '''
            class NoTitle:
                def export_set(self, dataset):
                    return "x"
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        record = _records_by_key()["notitle"]
        assert record.state is plugins.State.REJECTED
        assert "title" in record.detail

    def test_invalid_title_is_rejected(self, tmp_path, monkeypatch):
        _write(tmp_path, "badname.py", '''
            class BadName:
                title = "Bad-Name"

                def export_set(self, dataset):
                    return "x"
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        record = _records_by_key()["badname"]
        assert record.state is plugins.State.REJECTED
        assert "lowercase identifier" in record.detail
        assert not registry.has("Bad-Name")

    def test_format_without_transfer_methods_is_rejected(self):
        class Anchor:
            title = "anchor"

        with pytest.raises(InvalidFormatPlugin) as excinfo:
            validate_format(Anchor())
        assert "implements none of" in str(excinfo.value)

    def test_extensions_are_normalised(self):
        class Loud:
            title = "loud"
            extensions = "TXT"

            def export_set(self, dataset):
                return ""

        fmt = validate_format(Loud())
        assert fmt.extensions == ("txt",)

    def test_multiple_formats_in_one_module(self, tmp_path, monkeypatch):
        _write(tmp_path, "bundle.py", '''
            class First:
                title = "first"

                def export_set(self, dataset):
                    return "first-payload"

            class Second:
                title = "second"

                def export_set(self, dataset):
                    return "second-payload"

            TABLIB_FORMATS = [First, Second]
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("first") == "first-payload"
        assert _dataset().export("second") == "second-payload"
        assert sorted(plugins.registered_keys()) == ["first", "second"]

    def test_get_formats_factory(self, tmp_path, monkeypatch):
        _write(tmp_path, "factory.py", '''
            class Made:
                title = "made"

                def export_set(self, dataset):
                    return "made-payload"

            def get_formats():
                return [Made()]
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("made") == "made-payload"

    def test_autoscan_ignores_imported_classes(self, tmp_path, monkeypatch):
        _write(tmp_path / "lib", "helper.py", '''
            class ImportedFormat:
                title = "imported"

                def export_set(self, dataset):
                    return "should-not-register"
            ''')
        _write(tmp_path / "plugins", "scanned.py", '''
            from helper import ImportedFormat

            class Local:
                title = "local"

                def export_set(self, dataset):
                    return "local-payload"
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path / "plugins"))
        monkeypatch.syspath_prepend(str(tmp_path / "lib"))

        assert _dataset().export("local") == "local-payload"
        # The imported class does look like a format, it must still be ignored.
        import helper

        assert helper.ImportedFormat.title == "imported"
        assert not registry.has("imported")

    def test_extract_formats_reports_empty_declaration(self):
        import types

        module = types.ModuleType("bad_bundle")
        module.TABLIB_FORMATS = []
        with pytest.raises(InvalidFormatPlugin) as excinfo:
            extract_formats(module)
        assert "non-empty list" in str(excinfo.value)


# ----------------------------------------------------------------------
# conflicts
# ----------------------------------------------------------------------

class TestConflicts:
    def test_plugin_cannot_replace_builtin_by_default(self, tmp_path, monkeypatch):
        _write(tmp_path, "hijack.py", '''
            class Hijack:
                title = "csv"

                def export_set(self, dataset):
                    return "HIJACKED"
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("csv") == "name,qty\r\napple,3\r\npear|plum,5\r\n"
        record = _records_by_key()["csv"]
        assert record.state is plugins.State.REJECTED
        assert "already registered" in record.detail

    def test_reserved_dataset_attribute_is_refused_even_with_override(
            self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "shadow.py", '''
            class Shadow:
                title = "export"

                def export_set(self, dataset):
                    return "shadowed"
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            allow_override = true
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert _dataset().export("csv").startswith("name,qty")
        record = _records_by_key()["export"]
        assert record.state is plugins.State.REJECTED
        assert "collides with the existing Dataset attribute" in record.detail

    def test_allow_override_replaces_and_restores_builtin(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "tsv_override.py", '''
            class TsvOverride:
                title = "tsv"

                def export_set(self, dataset):
                    return "HIJACKED"
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            allow_override = true
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        assert _dataset().export("tsv") == "HIJACKED"

        monkeypatch.delenv("TABLIB_PLUGIN_CONFIG")
        plugins.reset()
        assert _dataset().export("tsv") == "name\tqty\r\napple\t3\r\npear|plum\t5\r\n"

    def test_second_plugin_with_same_key_is_rejected(self, tmp_path, monkeypatch):
        _write(tmp_path, "one.py", _format_source("shared", payload="first"))
        _write(tmp_path, "two.py", _format_source("shared", payload="second"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("shared") == "first"
        states = sorted(record.state.value for record in plugins.records())
        assert states == ["active", "rejected"]


# ----------------------------------------------------------------------
# error isolation
# ----------------------------------------------------------------------

class TestErrorIsolation:
    def test_import_error_is_contained(self, tmp_path, monkeypatch):
        _write(tmp_path, "a_boom.py", 'raise RuntimeError("kaboom at import")\n')
        _write(tmp_path, "b_good.py", _format_source("goodfmt"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("goodfmt") == "goodfmt-payload"
        assert _dataset().export("csv").startswith("name,qty")

        record = _records_by_key()["a_boom"]
        assert record.state is plugins.State.REJECTED
        assert "import failed" in record.detail

        diag = [item for item in plugins.diagnostics() if item.plugin == "a_boom"][0]
        assert diag.phase is plugins.Phase.IMPORT
        assert diag.error_type == "RuntimeError"
        assert "kaboom at import" in diag.message
        assert "RuntimeError" in diag.traceback

    def test_system_exit_at_import_is_contained(self, tmp_path, monkeypatch):
        _write(tmp_path, "suicide.py", 'import sys\nsys.exit(3)\n')
        _write(tmp_path, "ok.py", _format_source("survivor"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert _dataset().export("survivor") == "survivor-payload"
        diag = [item for item in plugins.diagnostics() if item.plugin == "suicide"][0]
        assert diag.error_type == "SystemExit"

    def test_runtime_error_is_reported_with_plugin_identity(self, tmp_path, monkeypatch):
        _write(tmp_path, "boom.py", '''
            class Boom:
                title = "boom"

                def export_set(self, dataset):
                    raise ValueError("kaboom at export")
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        with pytest.raises(PluginExecutionError) as excinfo:
            _dataset().export("boom")
        message = str(excinfo.value)
        assert "'boom'" in message
        assert "export_set()" in message
        assert "kaboom at export" in message

        assert _records_by_key()["boom"].failures == 1
        assert _dataset().export("json") == (
            '[{"name": "apple", "qty": 3}, {"name": "pear|plum", "qty": 5}]'
        )

    def test_detect_error_counts_as_no_match(self, tmp_path, monkeypatch):
        _write(tmp_path, "probe.py", '''
            class Probe:
                title = "probe"

                def export_set(self, dataset):
                    return ""

                def detect(self, stream):
                    raise RuntimeError("probe exploded")
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert tablib.detect_format(BUNK) is None
        assert _records_by_key()["probe"].failures == 1
        diag = [item for item in plugins.diagnostics()
                if item.error_type == "RuntimeError"][0]
        assert diag.phase is plugins.Phase.EXECUTION

    def test_quarantine_after_threshold_removes_format(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "boom.py", '''
            class Boom:
                title = "boom"

                def export_set(self, dataset):
                    raise ValueError("kaboom")
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            max_failures = 2
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        for _ in range(2):
            with pytest.raises(PluginExecutionError):
                _dataset().export("boom")

        assert registry.has("boom") is False
        assert hasattr(Dataset, "boom") is False
        with pytest.raises(PluginQuarantined) as excinfo:
            _dataset().export("boom")
        assert "quarantined" in str(excinfo.value)

        record = _records_by_key()["boom"]
        assert record.state is plugins.State.QUARANTINED
        assert record.failures == 2
        assert plugins.quarantine_reason("boom") == "2 failure(s), last one in export_set()"

    def test_quarantined_detect_stops_being_probed(self, tmp_path, monkeypatch):
        _write(tmp_path, "probe.py", '''
            class Probe:
                title = "probe"

                def export_set(self, dataset):
                    return ""

                def detect(self, stream):
                    raise RuntimeError("probe exploded")
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))
        monkeypatch.setenv("TABLIB_PLUGINS_MAX_FAILURES", "1")

        assert tablib.detect_format(BUNK) is None
        assert registry.has("probe") is False
        failures = len(plugins.diagnostics())
        assert tablib.detect_format(BUNK) is None
        assert len(plugins.diagnostics()) == failures

    def test_broken_detect_does_not_hide_working_plugin(self, tmp_path, monkeypatch):
        _write(tmp_path, "a_broken.py", '''
            class Broken:
                title = "broken"

                def export_set(self, dataset):
                    return ""

                def detect(self, stream):
                    raise RuntimeError("nope")
            ''')
        _write(tmp_path, "b_working.py", _format_source("workingfmt", marker="GOOD:"))
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))

        assert tablib.detect_format("GOOD:payload") == "workingfmt"

    def test_strict_mode_turns_probe_failures_into_errors(self, tmp_path, monkeypatch):
        _write(tmp_path, "probe.py", '''
            class Probe:
                title = "probe"

                def export_set(self, dataset):
                    return ""

                def detect(self, stream):
                    raise RuntimeError("probe exploded")
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_PATH", str(tmp_path))
        monkeypatch.setenv("TABLIB_PLUGINS_STRICT", "1")

        with pytest.raises(PluginExecutionError):
            tablib.detect_format(BUNK)

    def test_quarantined_override_falls_back_to_builtin(self, tmp_path, monkeypatch):
        _write(tmp_path / "plugins", "fragile_csv.py", '''
            class FragileCsv:
                title = "csv"

                def export_set(self, dataset):
                    raise RuntimeError("plugin is broken")
            ''')
        config = _write(tmp_path, "tablib-plugins.toml", '''
            [plugins]
            paths = ["plugins"]
            allow_override = true
            max_failures = 1
            ''')
        monkeypatch.setenv("TABLIB_PLUGIN_CONFIG", str(config))

        with pytest.raises(PluginExecutionError):
            _dataset().export("csv")

        # The built-in format must be back in place, not missing.
        assert _dataset().export("csv") == "name,qty\r\napple,3\r\npear|plum,5\r\n"


# ----------------------------------------------------------------------
# zero-plugin equivalence with upstream
# ----------------------------------------------------------------------

class TestUpstreamEquivalence:
    def test_builtin_keys_and_order_unchanged(self):
        plugins.load()
        assert list(registry.keys()) == [
            "json", "xlsx", "xls", "yaml", "csv", "tsv", "ods", "dbf",
            "html", "jira", "latex", "df", "rst", "sql", "cli",
        ]
        assert plugins.registered_keys() == []

    def test_builtin_text_exports_unchanged(self):
        data = _dataset()
        assert data.export("csv") == "name,qty\r\napple,3\r\npear|plum,5\r\n"
        assert data.export("json") == (
            '[{"name": "apple", "qty": 3}, {"name": "pear|plum", "qty": 5}]'
        )
        assert data.export("tsv") == "name\tqty\r\napple\t3\r\npear|plum\t5\r\n"
        assert data.export("yaml") == "- {name: apple, qty: 3}\n- {name: pear|plum, qty: 5}\n"
        assert data.export("html") == (
            "<table><thead><tr><th>name</th><th>qty</th></tr></thead><tbody>"
            "<tr><td>apple</td><td>3</td></tr><tr><td>pear|plum</td><td>5</td></tr>"
            "</tbody></table>"
        )
        assert data.export("jira") == "||name||qty||\n|apple|3|\n|pear|plum|5|"
        assert data.export("sql") == (
            "INSERT INTO export_table (name,qty) VALUES ('apple', 3);\n"
            "INSERT INTO export_table (name,qty) VALUES ('pear|plum', 5);\n"
        )

    def test_detect_format_unchanged(self):
        assert tablib.detect_format('a,b\n1,2\n') == "csv"
        assert tablib.detect_format('[{"a": 1}]') == "json"
        assert tablib.detect_format('a\tb\n1\t2') == "tsv"
        assert tablib.detect_format(BUNK) is None

    def test_unknown_format_message_unchanged(self):
        with pytest.raises(UnsupportedFormat) as excinfo:
            _dataset().export("nope")
        assert str(excinfo.value) == "Tablib has no format 'nope' or it is not registered."

    def test_diagnostics_empty_without_plugins(self):
        assert plugins.diagnostics() == ()
        assert plugins.records() == []
        assert plugins.doctor()["ok"] is True


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _run_cli(*args, cwd=None, env_extra=None):
    env = dict(os.environ)
    env.pop("TABLIB_PLUGIN_CONFIG", None)
    env.pop("TABLIB_PLUGIN_PATH", None)
    env["PYTHONPATH"] = str(Path(tablib.__file__).resolve().parents[1])
    env["PYTHONIOENCODING"] = "utf-8"
    # Keep CLI assertions independent from whatever is installed in the venv.
    env.setdefault("TABLIB_PLUGINS_ENTRY_POINTS", "0")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "tablib.plugins", *args],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=cwd,
    )


class TestCLI:
    def test_list_json_reports_plugin(self, tmp_path):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        result = _run_cli("list", "--json", env_extra={"TABLIB_PLUGIN_PATH": str(tmp_path)})

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert [item["key"] for item in payload["formats"]] == ["pipe"]
        assert payload["formats"][0]["state"] == "active"
        assert "csv" in payload["builtin_formats"]

    def test_doctor_exit_code_is_zero_when_healthy(self, tmp_path):
        _write(tmp_path, "pipe.py", _format_source("pipe"))
        result = _run_cli("doctor", env_extra={"TABLIB_PLUGIN_PATH": str(tmp_path)})

        assert result.returncode == 0, result.stderr
        assert "status: OK" in result.stdout
        assert "plugin entries : 1" in result.stdout

    def test_doctor_exit_code_is_one_with_broken_plugin(self, tmp_path):
        _write(tmp_path, "boom.py", 'raise RuntimeError("kaboom")\n')
        result = _run_cli("doctor", env_extra={"TABLIB_PLUGIN_PATH": str(tmp_path)})

        assert result.returncode == 1
        assert "status: DEGRADED" in result.stdout
        assert "kaboom" in result.stdout

    def test_paths_subcommand_shows_config(self, tmp_path):
        result = _run_cli("paths", "--json", env_extra={"TABLIB_PLUGIN_PATH": str(tmp_path)})

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["paths"] == [str(tmp_path)]
        assert payload["entry_point_group"] == "tablib.formats"


# ----------------------------------------------------------------------
# example plugins (installed separately, see examples/)
# ----------------------------------------------------------------------

@pytest.mark.real_entry_points
class TestExamplePlugins:
    def test_markdown_example_round_trips(self):
        pytest.importorskip("tablib_markdown_format")
        data = _dataset()

        exported = data.export("markdown")
        assert exported == (
            "| name | qty |\n"
            "| --- | --- |\n"
            "| apple | 3 |\n"
            "| pear\\|plum | 5 |"
        )

        restored = tablib.Dataset().load(exported, format="markdown")
        assert restored.headers == ["name", "qty"]
        assert list(restored[0]) == ["apple", "3"]
        assert tablib.detect_format(exported) == "markdown"

    def test_ndjson_example_round_trips(self):
        pytest.importorskip("tablib_ndjson_format")
        data = _dataset()

        exported = data.export("ndjson")
        assert exported.splitlines() == [
            '{"name": "apple", "qty": 3}',
            '{"name": "pear|plum", "qty": 5}',
        ]

        restored = tablib.Dataset().load(exported, format="ndjson")
        assert restored.headers == ["name", "qty"]
        assert restored.height == 2
        # Built-in formats are probed first, and the CSV sniffer claims
        # comma-separated lines.  Documented in docs/plugins.rst.
        assert tablib.detect_format(exported) == "csv"
        assert tablib.detect_format('{"a": 1}\n{"a": 2}') == "ndjson"
