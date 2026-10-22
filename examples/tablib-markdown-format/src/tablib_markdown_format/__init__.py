"""Markdown table format plugin for tablib.

Install::

    pip install -e examples/tablib-markdown-format

Then::

    >>> import tablib
    >>> data = tablib.Dataset(headers=["name", "qty"])
    >>> data.append(["apple", 3])
    >>> print(data.export("markdown"))
    | name | qty |
    | --- | --- |
    | apple | 3 |

The format also imports tables back::

    >>> tablib.Dataset().load("| a | b |\\n| --- | --- |\\n| 1 | 2 |", format="markdown").headers
    ['a', 'b']
"""

import re

__all__ = ["MarkdownFormat"]

_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_ALIGNMENTS = {"left", "center", "right"}


def _normalise_align(align, columns):
    if isinstance(align, str):
        align = [align] * columns
    align = list(align)[:columns]
    if len(align) < columns:
        align += ["left"] * (columns - len(align))
    for value in align:
        if value not in _ALIGNMENTS:
            raise ValueError(
                f"align must be one of {sorted(_ALIGNMENTS)}, got {value!r}"
            )
    return align


def _escape(cell):
    text = "" if cell is None else str(cell)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _unescape(cell):
    out = []
    index = 0
    while index < len(cell):
        char = cell[index]
        if char == "\\" and index + 1 < len(cell):
            out.append(cell[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out).replace("<br>", "\n")


def _split_row(line):
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells = []
    buffer = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            buffer.append(text[index:index + 2])
            index += 2
            continue
        if char == "|":
            cells.append(_unescape("".join(buffer)).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    cells.append(_unescape("".join(buffer)).strip())
    return cells


def _is_separator(line):
    cells = _split_row(line)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell.replace(" ", "")) for cell in cells)


class MarkdownFormat:
    """GitHub-flavoured Markdown pipe table."""

    title = "markdown"
    extensions = ("md", "markdown")

    DEFAULT_ALIGN = "left"

    def __init__(self):
        self.align = self.DEFAULT_ALIGN

    def configure(self, options):
        """Accept ``align`` from ``[plugins.options.markdown]``."""
        align = options.get("align", self.DEFAULT_ALIGN)
        if not isinstance(align, (str, list, tuple)):
            raise ValueError("align must be a string or a list of strings")
        for value in ([align] if isinstance(align, str) else align):
            if value not in _ALIGNMENTS:
                raise ValueError(f"unknown alignment {value!r}")
        self.align = align

    def export_set(self, dataset, **kwargs):
        align = kwargs.pop("align", self.align)
        rows = [list(row) for row in dataset._package(dicts=False)]
        if not rows:
            return ""
        columns = max(len(row) for row in rows)
        aligns = _normalise_align(align, columns)

        lines = []
        body = rows
        if dataset.headers:
            lines.append(self._row(rows[0], columns))
            lines.append("| " + " | ".join(self._separator(value) for value in aligns) + " |")
            body = rows[1:]
        for row in body:
            lines.append(self._row(row, columns))
        return "\n".join(lines)

    def import_set(self, dset, in_stream, headers=True, **kwargs):
        text = in_stream.read() if hasattr(in_stream, "read") else str(in_stream)
        rows = []
        for index, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            if index == 1 and _is_separator(line):
                continue
            rows.append(_split_row(line))
        dset.wipe()
        if not rows:
            return
        if headers:
            dset.headers = rows[0]
            rows = rows[1:]
        for row in rows:
            dset.append(row)

    def detect(self, stream):
        text = stream.read(4096)
        if not isinstance(text, str):
            text = text.decode("utf-8", "replace")
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        if not all(line.lstrip().startswith("|") for line in lines):
            return False
        return _is_separator(lines[1])

    @staticmethod
    def _row(row, columns):
        cells = list(row) + [""] * (columns - len(row))
        return "| " + " | ".join(_escape(cell) for cell in cells) + " |"

    @staticmethod
    def _separator(align):
        return {"left": "---", "center": ":---:", "right": "---:"}[align]
