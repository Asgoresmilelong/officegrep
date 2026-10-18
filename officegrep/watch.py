"""目录监听：轮询式增量索引。

不用 watchdog：那会引入平台相关的原生依赖，而这里的瓶颈从来不是事件延迟，
而是解析大文件的耗时。两秒轮询 + stat 比对，在几千个文件规模下开销可以忽略。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from .extract import SUPPORTED
from .index import Index


def snapshot(roots: Iterable[Path]) -> dict[Path, tuple[int, float]]:
    """收集 (大小, mtime) 快照；只读 stat，不解析文件。"""
    out: dict[Path, tuple[int, float]] = {}
    for raw in roots:
        root = Path(raw).resolve()
        if root.is_file():
            try:
                st = root.stat()
            except OSError:
                continue
            out[root] = (st.st_size, st.st_mtime)
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in SUPPORTED or not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            out[path] = (st.st_size, st.st_mtime)
    return out


class Watcher:
    def __init__(
        self,
        index: Index,
        roots: Iterable[str | Path],
        interval: float = 2.0,
        on_change: Callable[[str, Path, str], None] | None = None,
    ):
        self.index = index
        self.roots = [Path(r) for r in roots]
        self.interval = max(0.2, float(interval))
        self.on_change = on_change
        # 初始快照留空：第一次 poll 会扫描全部文件，交给 Index.needs_update 判断
        # （大小/mtime 相同就跳过）。这样「启动时补齐已有文件」和「运行中增量」是同一套逻辑。
        self._snapshot: dict[Path, tuple[int, float]] = {}

    def _emit(self, kind: str, path: Path, note: str = "") -> None:
        if self.on_change:
            self.on_change(kind, path, note)

    def poll(self) -> dict:
        """扫一次：新增/修改的文件重新索引，消失的文件从索引里删掉。"""
        current = snapshot(self.roots)
        stats = {"changed": 0, "removed": 0, "failed": 0}

        for path, sig in current.items():
            if self._snapshot.get(path) == sig:
                continue
            try:
                blocks, updated = self.index.add_file(path)
            except Exception as exc:  # 单个坏文件不中断监听
                stats["failed"] += 1
                self._emit("failed", path, str(exc))
                continue
            if updated:
                stats["changed"] += 1
                self._emit("indexed", path, f"{blocks} blocks")

        for path in set(self._snapshot) - set(current):
            if self.index.remove_file(path):
                stats["removed"] += 1
                self._emit("removed", path)

        self._snapshot = current
        return stats

    def run(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        while not stop_event.is_set():
            self.poll()
            stop_event.wait(self.interval)

    def start_background(self) -> tuple[threading.Thread, threading.Event]:
        """在后台线程里跑监听。

        sqlite3 的连接默认不能跨线程使用，而 MCP 服务是在主线程里读同一个库，
        所以这里给监听线程单独开一个连接（WAL 模式下读写可并发）。
        """
        clone = Index(self.index.db_path)
        runner = Watcher(clone, self.roots, self.interval, self.on_change)
        stop = threading.Event()
        thread = threading.Thread(target=runner.run, args=(stop,), name="officegrep-watch", daemon=True)
        thread.start()
        return thread, stop


def parse_roots(raw: str | None) -> list[str]:
    """拆环境变量里的路径列表。只用 `;` 分隔，因为 Windows 盘符里带冒号。"""
    if not raw:
        return []
    return [p.strip() for p in raw.split(";") if p.strip()]
