"""MCPB 入口：把 bundle 根目录放进 sys.path，再交给 officegrep 的 CLI。"""

import sys
from pathlib import Path

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BUNDLE_ROOT))

from officegrep.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["mcp"]))
