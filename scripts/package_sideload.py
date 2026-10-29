"""Package custom_components/qfnplus into dist/qfnplus-sideload.zip.

The zip root contains the `qfnplus/` folder so it can be extracted directly
into /config/custom_components/ on HAOS. Cross-platform, no deps:
    python scripts/package_sideload.py
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "custom_components" / "qfnplus"
DIST = ROOT / "dist"
OUT = DIST / "qfnplus-sideload.zip"
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}


def main() -> None:
    DIST.mkdir(exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    files = sorted(
        p
        for p in SRC.rglob("*")
        if p.is_file()
        and not (EXCLUDE_DIRS & set(p.parts))
        and p.name not in EXCLUDE_FILES
        and p.suffix != ".pyc"
    )
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(SRC.parent))
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"packed {len(files)} files -> {OUT}")
    print(f"sha256: {digest}")


if __name__ == "__main__":
    main()
