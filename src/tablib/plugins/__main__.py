""" Tablib - plugin diagnostics CLI.

Usage::

    python -m tablib.plugins list [--json]
    python -m tablib.plugins doctor [--json]
    python -m tablib.plugins paths [--json]
    python -m tablib.plugins config [--json]

``doctor`` exits with status 1 when a plugin was rejected or quarantined, which
makes it usable as a deployment health check.
"""

__lazy_modules__ = {
    "argparse",
    "json",
    "sys",
    "tablib",
    "tablib.formats",
}

import argparse
import json
import sys

import tablib
from tablib.formats import registry


def _configure_stdout():
    stream = getattr(sys.stdout, "reconfigure", None)
    if stream is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - closed pipe
            pass


def _table(rows, headers):
    if not rows:
        return "  (none)"
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = [
        "  " + "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)),
        "  " + "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append("  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


def _report():
    tablib.plugins.load()
    data = tablib.plugins.doctor()
    plugin_keys = {record["key"] for record in data["formats"]}
    builtin = [key for key in registry.keys() if key not in plugin_keys]
    data["builtin_formats"] = builtin
    return data


def cmd_list(args):
    data = _report()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print(f"built-in formats ({len(data['builtin_formats'])}):")
    print(_table([[key] for key in data["builtin_formats"]], ["key"]))
    print()
    print(f"plugins ({len(data['formats'])} discovered entries):")
    rows = [
        [
            record["key"],
            record["state"],
            record["source"],
            record["failures"],
            record["origin"],
        ]
        for record in data["formats"]
    ]
    print(_table(rows, ["key", "state", "source", "fails", "origin"]))
    return 0


def cmd_doctor(args):
    data = _report()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0 if data["ok"] else 1

    settings = data["settings"]
    print("tablib plugin doctor")
    print(f"  config file    : {settings['source'] or '(defaults)'}")
    print(f"  subsystem      : {'enabled' if settings['enabled'] else 'DISABLED'}")
    print(f"  entry points   : {'on' if settings['entry_points'] else 'off'}")
    print(f"  strict         : {'on' if settings['strict'] else 'off'}")
    print(f"  allow_override : {'on' if settings['allow_override'] else 'off'}")
    print(f"  max_failures   : {settings['max_failures']}")
    print(f"  search paths   : {', '.join(settings['paths']) or '(none)'}")
    print(f"  disabled       : {', '.join(settings['disabled']) or '(none)'}")
    print(f"  discovered     : {data['discovered']}")
    print(f"  built-in keys  : {len(data['builtin_formats'])}")
    print(f"  plugin entries : {len(data['formats'])}")
    print()
    if data["diagnostics"]:
        print(f"diagnostics ({len(data['diagnostics'])}):")
        for item in data["diagnostics"]:
            print(f"  - [{item['phase']}] {item['plugin']}: {item['error_type']}: "
                  f"{item['message']}")
            if item["origin"]:
                print(f"      origin: {item['origin']}")
    else:
        print("diagnostics: (none)")
    print()
    print("status:", "OK" if data["ok"] else "DEGRADED")
    return 0 if data["ok"] else 1


def cmd_paths(args):
    data = _report()
    payload = {
        "config": data["settings"]["source"] or None,
        "paths": data["settings"]["paths"],
        "entry_point_group": "tablib.formats",
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"config file: {payload['config'] or '(defaults)'}")
    print(f"entry point group: {payload['entry_point_group']}")
    if payload["paths"]:
        print("search paths:")
        for path in payload["paths"]:
            print(f"  - {path}")
    else:
        print("search paths: (none)")
    return 0


def cmd_config(args):
    data = _report()
    if args.json:
        print(json.dumps(data["settings"], indent=2, ensure_ascii=False))
        return 0
    for key, value in data["settings"].items():
        print(f"{key} = {value!r}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m tablib.plugins",
        description="Inspect tablib format plugins.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler, help_text in (
        ("list", cmd_list, "list built-in formats and plugins"),
        ("doctor", cmd_doctor, "configuration and failure report"),
        ("paths", cmd_paths, "show the resolved config file and search paths"),
        ("config", cmd_config, "print the effective plugin settings"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="machine-readable output")
        sub.set_defaults(handler=handler)
    return parser


def main(argv=None):
    _configure_stdout()
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
