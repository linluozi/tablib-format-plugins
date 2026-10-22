"""Newline-delimited JSON (NDJSON / JSON Lines) format plugin for tablib.

Install::

    pip install -e examples/tablib-ndjson-format

Then::

    >>> import tablib
    >>> data = tablib.Dataset(headers=["name", "qty"])
    >>> data.append(["apple", 3])
    >>> print(data.export("ndjson"))
    {"name": "apple", "qty": 3}
"""

import json

__all__ = ["NDJSONFormat"]


class NDJSONFormat:
    """One JSON document per line."""

    title = "ndjson"
    extensions = ("ndjson", "jsonl")

    def export_set(self, dataset, **kwargs):
        ensure_ascii = kwargs.pop("ensure_ascii", False)
        if dataset.headers:
            records = dataset.dict
        else:
            records = [list(row) for row in dataset._package(dicts=False)]
        return "\n".join(
            json.dumps(record, ensure_ascii=ensure_ascii, default=str)
            for record in records
        )

    def import_set(self, dset, in_stream, **kwargs):
        text = in_stream.read() if hasattr(in_stream, "read") else str(in_stream)
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        dset.wipe()
        if not records:
            return
        if isinstance(records[0], dict):
            headers = list(records[0])
            dset.headers = headers
            for record in records:
                dset.append([record.get(header) for header in headers])
        else:
            for record in records:
                dset.append(record)

    def detect(self, stream):
        text = stream.read(4096)
        if not isinstance(text, str):
            text = text.decode("utf-8", "replace")
        lines = [line for line in text.splitlines() if line.strip()]
        # A single JSON document is the built-in "json" format's job.
        if len(lines) < 2:
            return False
        try:
            for line in lines:
                if not isinstance(json.loads(line), (dict, list)):
                    return False
        except ValueError:
            return False
        return True
