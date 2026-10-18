"""SQLite FTS5 索引：增量、离线、单文件数据库。

中文检索的取舍：FTS5 默认 tokenizer 会把整句中文当一个词，不可用；
trigram tokenizer 支持中英文子串匹配，但查询短于 3 个字符时无法命中，
因此对 1–2 字的查询回退到 LIKE 扫描（个人语料规模下可接受）。
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .extract import SUPPORTED, Block, extract

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    digest     TEXT NOT NULL,
    blocks     INTEGER NOT NULL,
    indexed_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS blocks USING fts5(
    path UNINDEXED,
    loc  UNINDEXED,
    body,
    tokenize = 'trigram'
);
"""


@dataclass
class Hit:
    path: str
    loc: str
    snippet: str
    score: float


def _digest(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _snippet_around(body: str, needle_lower: str, width: int = 48) -> str:
    """以命中位置为中心截取片段，高亮用方括号。"""
    flat = " ".join(body.split())
    pos = flat.lower().find(needle_lower)
    if pos < 0:
        return flat[:width * 2]
    start = max(0, pos - width)
    end = min(len(flat), pos + len(needle_lower) + width)
    head = "…" if start > 0 else ""
    tail = "…" if end < len(flat) else ""
    hit_end = pos + len(needle_lower)
    return f"{head}{flat[start:pos]}[{flat[pos:hit_end]}]{flat[hit_end:end]}{tail}"


class Index:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------------------------------------------------- 写入

    def _stored(self, path: str):
        return self.conn.execute(
            "SELECT size, mtime, digest FROM files WHERE path = ?", (path,)
        ).fetchone()

    def needs_update(self, path: Path) -> bool:
        row = self._stored(str(path))
        if row is None:
            return True
        size, mtime, digest = row
        stat = path.stat()
        if int(stat.st_size) == size and abs(stat.st_mtime - mtime) < 1e-6:
            return False
        return _digest(path) != digest

    def add_file(self, path: Path) -> tuple[int, bool]:
        """返回 (块数, 是否真的更新了)。"""
        path = path.resolve()
        if not self.needs_update(path):
            return 0, False
        blocks = extract(path)
        stat = path.stat()
        self.conn.execute("DELETE FROM blocks WHERE path = ?", (str(path),))
        self.conn.executemany(
            "INSERT INTO blocks (path, loc, body) VALUES (?, ?, ?)",
            [(str(path), b.loc, b.text) for b in blocks],
        )
        self.conn.execute(
            "INSERT INTO files (path, size, mtime, digest, blocks, indexed_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, "
            "digest=excluded.digest, blocks=excluded.blocks, indexed_at=excluded.indexed_at",
            (str(path), int(stat.st_size), stat.st_mtime, _digest(path), len(blocks), time.time()),
        )
        self.conn.commit()
        return len(blocks), True

    def remove_file(self, path: Path) -> bool:
        key = str(path.resolve())
        cur = self.conn.execute("DELETE FROM files WHERE path = ?", (key,))
        self.conn.execute("DELETE FROM blocks WHERE path = ?", (key,))
        self.conn.commit()
        return cur.rowcount > 0

    def prune(self, root: Path) -> int:
        """删除索引里已不存在的文件。"""
        root = root.resolve()
        gone = [
            Path(row[0])
            for row in self.conn.execute("SELECT path FROM files")
            if not Path(row[0]).exists()
        ]
        for path in gone:
            self.remove_file(path)
        return len(gone)

    def index_paths(self, paths, follow_symlinks: bool = False, on_file=None) -> dict:
        files: list[Path] = []
        for raw in paths:
            p = Path(raw).resolve()
            if p.is_file():
                files.append(p)
            elif p.is_dir():
                for child in p.rglob("*"):
                    if child.is_file() and child.suffix.lower() in SUPPORTED:
                        if child.is_symlink() and not follow_symlinks:
                            continue
                        files.append(child)
        total = added = skipped = failed = 0
        blocks = 0
        for path in sorted(set(files)):
            total += 1
            try:
                count, changed = self.add_file(path)
            except Exception as exc:  # 单个坏文件不应中断整批
                failed += 1
                if on_file:
                    on_file(path, f"FAILED: {exc}")
                continue
            if changed:
                added += 1
                blocks += count
                if on_file:
                    on_file(path, f"{count} blocks")
            else:
                skipped += 1
        return {"total": total, "indexed": added, "skipped": skipped, "failed": failed, "blocks": blocks}

    # ---------------------------------------------------------- 查询

    def search(self, query: str, limit: int = 10, under: str | None = None) -> list[Hit]:
        q = query.strip()
        if not q:
            return []
        prefix = f"{Path(under).resolve()}%" if under else "%"
        if len(q) >= 3:
            rows = self.conn.execute(
                "SELECT path, loc, snippet(blocks, 2, '[', ']', '…', 12), bm25(blocks) "
                "FROM blocks WHERE blocks MATCH ? AND path LIKE ? "
                "ORDER BY bm25(blocks) LIMIT ?",
                ('"' + q.replace('"', '""') + '"', prefix, limit),
            ).fetchall()
            return [Hit(path=r[0], loc=r[1], snippet=r[2], score=float(r[3])) for r in rows]

        # 1-2 字查询：trigram 无法命中，回退到扫描 + 自定义排序
        rows = self.conn.execute(
            "SELECT path, loc, body FROM blocks WHERE body LIKE ? ESCAPE '\\' AND path LIKE ? LIMIT 2000",
            (f"%{q}%", prefix),
        ).fetchall()
        scored = []
        needle = q.lower()
        for path, loc, body in rows:
            hits = body.lower().count(needle)
            if hits:
                scored.append((hits, path, loc, _snippet_around(body, needle)))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [Hit(path=p, loc=loc, snippet=snip, score=float(hits)) for hits, p, loc, snip in scored[:limit]]

    def list_files(self, under: str | None = None) -> list[tuple[str, int]]:
        prefix = f"{Path(under).resolve()}%" if under else "%"
        return self.conn.execute(
            "SELECT path, blocks FROM files WHERE path LIKE ? ORDER BY path", (prefix,)
        ).fetchall()

    def stats(self) -> dict:
        files, blocks = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM files), (SELECT COUNT(*) FROM blocks)"
        ).fetchone()
        return {
            "db": str(self.db_path),
            "db_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
            "files": files,
            "blocks": blocks,
        }

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
