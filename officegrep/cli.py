"""命令行入口：index / search / stats / mcp。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .index import Index
from .mcp import Server
from .watch import Watcher, parse_roots


def default_db() -> Path:
    env = os.environ.get("OFFICEGREP_DB")
    return Path(env) if env else Path.home() / ".officegrep" / "index.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="officegrep", description="给 agent 用的本地文档全文检索（Word/PPT/Excel/PDF/文本）")
    parser.add_argument("--version", action="version", version=f"officegrep {__version__}")
    parser.add_argument("--db", default=str(default_db()), help="索引数据库路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="建立或更新索引")
    p_index.add_argument("paths", nargs="+", help="目录或文件")

    p_search = sub.add_parser("search", help="检索")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--under", help="只搜这个目录下的文件")
    p_search.add_argument("--json", action="store_true")

    sub.add_parser("stats", help="索引统计")
    sub.add_parser("mcp", help="以 MCP 服务器方式运行（stdio）")

    p_watch = sub.add_parser("watch", help="监听目录变化并增量更新索引")
    p_watch.add_argument("paths", nargs="+", help="目录或文件")
    p_watch.add_argument("--interval", type=float, default=2.0, help="轮询间隔（秒）")
    p_watch.add_argument("--once", action="store_true", help="只扫描一次后退出")
    return parser


def cmd_index(index: Index, args) -> int:
    def progress(path: Path, note: str) -> None:
        print(f"  {path}  {note}", file=sys.stderr)

    stats = index.index_paths(args.paths, on_file=progress)
    print(
        f"扫描 {stats['total']} · 更新 {stats['indexed']} · 跳过 {stats['skipped']} · "
        f"失败 {stats['failed']} · 新增文本块 {stats['blocks']}"
    )
    return 0 if stats["failed"] == 0 else 1


def cmd_search(index: Index, args) -> int:
    hits = index.search(args.query, limit=args.limit, under=args.under)
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], ensure_ascii=False, indent=2))
        return 0 if hits else 1
    if not hits:
        print(f"没有找到包含「{args.query}」的内容。", file=sys.stderr)
        return 1
    print(f"找到 {len(hits)} 条：")
    for hit in hits:
        print(f"\n{hit.path}  [{hit.loc}]")
        print(f"  {hit.snippet}")
    return 0


def cmd_stats(index: Index, _args) -> int:
    stats = index.stats()
    print(f"数据库：{stats['db']}（{stats['db_bytes'] / 1024:.1f} KiB）")
    print(f"文件 {stats['files']} 个，文本块 {stats['blocks']} 个")
    return 0


def cmd_watch(index: Index, args) -> int:
    def report(kind: str, path: Path, note: str = "") -> None:
        print(f"[{kind}] {path} {note}".rstrip(), file=sys.stderr)

    watcher = Watcher(index, args.paths, interval=args.interval, on_change=report)
    if args.once:
        stats = watcher.poll()
        print(f"扫描一次：更新 {stats['changed']} · 删除 {stats['removed']} · 失败 {stats['failed']}")
        return 0
    print(f"监听中（每 {watcher.interval}s）：{', '.join(str(r) for r in watcher.roots)}", file=sys.stderr)
    try:
        watcher.run()
    except KeyboardInterrupt:
        print("已停止", file=sys.stderr)
    return 0


def cmd_mcp(index: Index, _args) -> int:
    roots = parse_roots(os.environ.get("OFFICEGREP_WATCH"))
    if roots:
        Watcher(index, roots).start_background()
        print(f"watch: {', '.join(roots)}", file=sys.stderr)
    print(f"officegrep MCP server · db={index.db_path}", file=sys.stderr)
    Server(index).serve()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    index = Index(args.db)
    try:
        handlers = {
            "index": cmd_index,
            "search": cmd_search,
            "stats": cmd_stats,
            "mcp": cmd_mcp,
            "watch": cmd_watch,
        }
        return handlers[args.command](index, args)
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
