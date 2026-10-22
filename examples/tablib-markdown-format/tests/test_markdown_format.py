
import pytest
from tablib_markdown_format import MarkdownFormat

import tablib


def _dataset():
    data = tablib.Dataset(headers=["name", "qty"])
    data.append(["apple", 3])
    data.append(["pear|plum", 5])
    return data


def test_entry_point_registers_the_format():
    data = _dataset()
    assert data.export("markdown") == (
        "| name | qty |\n| --- | --- |\n| apple | 3 |\n| pear\\|plum | 5 |"
    )


def test_pipes_are_escaped_and_unescaped():
    exported = _dataset().export("markdown")
    restored = tablib.Dataset().load(exported, format="markdown")
    assert list(restored[1]) == ["pear|plum", "5"]


def test_round_trip_keeps_headers_and_height():
    restored = tablib.Dataset().load(_dataset().export("markdown"), format="markdown")
    assert restored.headers == ["name", "qty"]
    assert restored.height == 2


def test_detect_recognises_a_pipe_table():
    assert tablib.detect_format("| a | b |\n| --- | --- |\n| 1 | 2 |") == "markdown"
    assert MarkdownFormat().detect(open(__file__, encoding="utf-8")) is False


def test_alignment_option():
    assert _dataset().export("markdown", align="center").splitlines()[1] == (
        "| :---: | :---: |"
    )


def test_configure_rejects_unknown_alignment():
    with pytest.raises(ValueError):
        MarkdownFormat().configure({"align": "justify"})
