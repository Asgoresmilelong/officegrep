"""首次启动的自动建库：索引为空时，后台扫默认目录。

目标是让「装完就能用」成立——用户不必先知道 `officegrep index` 这条命令。
扫描在后台线程里跑，用独立连接（sqlite3 连接不能跨线程），不阻塞 MCP 握手。
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from .extract import SUPPORTED
from .index import Index

DEFAULT_SUBDIRS = ("Documents", "Downloads", "Desktop")
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 跳过超大文件，避免首次启动卡在解析上

_state = {"running": False, "done": False, "indexed": 0, "failed": 0, "roots": []}
_lock = threading.Lock()


def autoindex_enabled() -> bool:
    raw = os.environ.get("OFFICEGREP_AUTOINDEX", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def default_roots(home: str | Path | None = None) -> list[Path]:
    base = Path(home or Path.home())
    found = [base / name for name in DEFAULT_SUBDIRS if (base / name).is_dir()]
    return found or ([base] if base.is_dir() else [])


def files_under(paths, max_bytes: int | None = None) -> list[Path]:
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        root = Path(raw).expanduser()
        candidates = [root] if root.is_file() else root.rglob("*")
        for path in candidates:
            if path.suffix.lower() not in SUPPORTED or not path.is_file():
                continue
            if max_bytes is not None:
                try:
                    if path.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
    return out


def bootstrap(
    index: Index,
    roots=None,
    max_bytes: int | None = DEFAULT_MAX_BYTES,
    on_file=None,
) -> dict:
    roots = [Path(r) for r in (roots if roots is not None else default_roots())]
    stats = {"files": 0, "indexed": 0, "skipped": 0, "failed": 0, "blocks": 0}

    for path in files_under(roots, max_bytes=max_bytes):
        stats["files"] += 1
        try:
            blocks, updated = index.add_file(path)
        except Exception as exc:  # 坏文件不中断整批
            stats["failed"] += 1
            if on_file:
                on_file(path, f"FAILED: {exc}")
            continue
        if updated:
            stats["indexed"] += 1
            stats["blocks"] += blocks
            if on_file:
                on_file(path, f"{blocks} blocks")
        else:
            stats["skipped"] += 1
    return stats


def status() -> dict:
    with _lock:
        return dict(_state)


def start(
    db_path: str | Path,
    roots=None,
    max_bytes: int | None = DEFAULT_MAX_BYTES,
    on_file=None,
    on_done=None,
) -> threading.Thread:
    roots = [Path(r) for r in (roots if roots is not None else default_roots())]
    with _lock:
        _state.update({"running": True, "done": False, "indexed": 0, "failed": 0,
                       "roots": [str(r) for r in roots]})

    def run() -> None:
        index = Index(db_path)

        def progress(path, note: str) -> None:
            if on_file:
                on_file(path, note)
            if "blocks" in note:  # 让 hint 里的进度是实时的
                with _lock:
                    _state["indexed"] += 1

        try:
            stats = bootstrap(index, roots, max_bytes=max_bytes, on_file=progress)
        except Exception as exc:  # 极端情况下也别让线程静默死掉
            print(f"autoindex 失败：{exc}", file=sys.stderr)
            stats = {"indexed": 0, "failed": 1}
        finally:
            index.close()
        with _lock:
            _state.update({"running": False, "done": True,
                           "indexed": stats["indexed"], "failed": stats["failed"]})
        if on_done:
            on_done(stats)

    thread = threading.Thread(target=run, name="officegrep-bootstrap", daemon=True)
    thread.start()
    return thread


def hint_if_building() -> str:
    """索引还在建时，给 agent 一句可解释的提示。"""
    state = status()
    if state["running"]:
        roots = "、".join(state["roots"]) or "默认目录"
        return f"（索引正在后台建立，已索引 {state['indexed']} 个文件，扫描范围：{roots}。请稍后重试。）"
    return ""
