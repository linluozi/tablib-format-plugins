import io
import json

from tablib_ndjson_format import NDJSONFormat

import tablib


def _dataset():
    data = tablib.Dataset(headers=["name", "qty"])
    data.append(["apple", 3])
    data.append(["pear|plum", 5])
    return data


def test_export_one_document_per_line():
    lines = _dataset().export("ndjson").splitlines()
    assert lines == [
        '{"name": "apple", "qty": 3}',
        '{"name": "pear|plum", "qty": 5}',
    ]
    assert [json.loads(line)["name"] for line in lines] == ["apple", "pear|plum"]


def test_import_rebuilds_headers():
    restored = tablib.Dataset().load('{"a": 1, "b": 2}\n{"a": 3, "b": 4}', format="ndjson")
    assert restored.headers == ["a", "b"]
    assert list(restored[1]) == [3, 4]


def test_detect_requires_multiple_lines():
    assert tablib.detect_format('{"a": 1}\n{"a": 2}') == "ndjson"
    assert NDJSONFormat().detect(io.StringIO('{"a": 1}')) is False


def test_dataset_without_headers_exports_arrays():
    data = tablib.Dataset()
    data.append(["apple", 3])
    assert data.export("ndjson") == '["apple", 3]'
