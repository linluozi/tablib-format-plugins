# tablib-markdown-format

Markdown pipe table support for [tablib](https://github.com/jazzband/tablib),
packaged as a tablib format plugin.

```bash
pip install -e examples/tablib-markdown-format
```

```python
import tablib

data = tablib.Dataset(headers=["name", "qty"])
data.append(["apple", 3])
data.append(["pear|plum", 5])

print(data.export("markdown"))
```

```markdown
| name | qty |
| --- | --- |
| apple | 3 |
| pear\|plum | 5 |
```

Import works the other way round, and auto-detection recognises the table:

```python
tablib.Dataset().load("| a | b |\n| --- | --- |\n| 1 | 2 |", format="markdown")
tablib.detect_format("| a | b |\n| --- | --- |\n| 1 | 2 |")   # 'markdown'
```

## Options

The plugin receives the `[plugins.options.markdown]` table from the tablib
plugin config through its `configure()` hook:

```toml
[plugins.options.markdown]
align = "center"          # or ["left", "center", "right"]
```

`align` may also be passed per call: `data.export("markdown", align="right")`.

## Why this is a plugin, not a patch

`tablib.formats.registry` only knows about formats shipped inside tablib. This
package declares the `tablib.formats` entry point, so installing it is enough:
no fork, no monkeypatching, and `pip uninstall` removes the format again.
