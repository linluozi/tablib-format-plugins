"""Build the delivery ZIP for the tablib plugin system.

Rules learned the hard way:

* text files are normalised to LF, binaries are copied verbatim - a blanket
  ``replace(b"\\r\\n", b"\\n")`` corrupts PNG/zip payloads;
* every entry shares one top-level directory so the archive never explodes
  into the extraction root;
* the archive is deterministic (sorted paths, fixed timestamps) so the same
  tree produces the same bytes.
"""

import os
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PREFIX = "tablib-plugin-system"
EXCLUDE_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".tox",
    "htmlcov", ".mypy_cache", "build", "dist", ".venv", "venv", ".idea",
}
EXCLUDE_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".egg-info", ".coverage", ".orig", ".rej", ".zip",
}
EXCLUDE_FILES = {
    ".coverage", ".DS_Store", "coverage.xml", "SHA256SUMS.txt", "_version.py",
}


def wanted(path):
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return False
    if path.name in EXCLUDE_FILES:
        return False
    if path.suffix in EXCLUDE_SUFFIXES:
        return False
    if any(part.endswith(".egg-info") for part in path.parts):
        return False
    return True


def normalise(data, suffix):
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".whl", ".xlsx"}:
        return data
    if b"\x00" in data:  # binary payload without a telling extension
        return data
    return data.replace(b"\r\n", b"\n")


def main(argv):
    target = Path(argv[1]) if len(argv) > 1 else REPO.parent / f"{PREFIX}.zip"
    files = sorted(
        path for path in REPO.rglob("*") if path.is_file() and wanted(path.relative_to(REPO))
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(REPO)
            data = normalise(path.read_bytes(), path.suffix.lower())
            info = zipfile.ZipInfo(f"{PREFIX}/{relative.as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (path.stat().st_mode & 0xFFFF) << 16
            archive.writestr(info, data)
    size = target.stat().st_size
    print(f"{target}  {len(files)} files  {size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
