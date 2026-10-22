# tablib-ndjson-format

Newline-delimited JSON (NDJSON / JSON Lines) support for
[tablib](https://github.com/jazzband/tablib), packaged as a tablib format plugin.

```bash
pip install -e examples/tablib-ndjson-format
```

```python
import tablib

data = tablib.Dataset(headers=["name", "qty"])
data.append(["apple", 3])
data.append(["pear|plum", 5])

print(data.export("ndjson"))
```

```text
{"name": "apple", "qty": 3}
{"name": "pear|plum", "qty": 5}
```

NDJSON is what log shippers, `jq -c` and streaming importers expect: one
self-contained JSON document per line, so a consumer can process the file
without loading it entirely.

```python
tablib.Dataset().load('{"a": 1}\n{"a": 2}', format="ndjson").height   # 2
tablib.detect_format('{"a": 1}\n{"a": 2}')                            # 'ndjson'
```

A single JSON document is deliberately *not* detected as NDJSON - that is the
built-in `json` format's job.
