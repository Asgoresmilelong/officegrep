"""把 mcpb/ 与 officegrep 包打成 .mcpb（一个 zip），输出路径与 sha256。

    python scripts/build_mcpb.py [输出目录]
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "mcpb"
PACKAGE = ROOT / "officegrep"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def add(zf: zipfile.ZipFile, arcname: str, source: Path) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, source.read_bytes())


def main() -> int:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"officegrep-{version}.mcpb"

    with zipfile.ZipFile(out, "w") as zf:
        add(zf, "manifest.json", BUNDLE / "manifest.json")
        add(zf, "pyproject.toml", BUNDLE / "pyproject.toml")
        add(zf, "server/main.py", BUNDLE / "server" / "main.py")
        for py in sorted(PACKAGE.glob("*.py")):
            add(zf, f"officegrep/{py.name}", py)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"bundle: {out}")
    print(f"bytes:  {out.stat().st_size}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
