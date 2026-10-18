"""极简 MCP 服务器：stdio + 换行分隔的 JSON-RPC 2.0，零第三方依赖。

只实现 tools 能力（initialize / tools/list / tools/call），这已覆盖
Claude Desktop、Cursor、Codex、DSH 等客户端的文档检索场景。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .bootstrap import hint_if_building
from .index import Index

PROTOCOL_VERSION = "2025-06-18"
MAX_CHARS = 7000

TOOLS = [
    {
        "name": "search_docs",
        "description": "在本地 Word/PPT/Excel/PDF/文本文件中全文检索，返回文件路径、定位符（段落/页/幻灯片/单元格）与命中片段。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索词，支持中英文；>=3 字符走 FTS5 索引，1-2 字符走扫描"},
                "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
                "folder": {"type": "string", "description": "可选：只搜这个目录下的文件"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_docs",
        "description": "列出已建立索引的文件及其文本块数量。",
        "inputSchema": {
            "type": "object",
            "properties": {"folder": {"type": "string", "description": "可选：只看这个目录"}},
        },
    },
    {
        "name": "index_folder",
        "description": "为指定目录（或文件）建立/更新索引，增量处理，已索引且未修改的文件会跳过。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录或文件路径"}},
            "required": ["path"],
        },
    },
]


def _ok(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text[:MAX_CHARS]}], "isError": is_error}


class Server:
    def __init__(self, index: Index):
        self.index = index

    # ------------------------------------------------------------ tools

    def _tool_search(self, args: dict) -> dict:
        query = (args.get("query") or "").strip()
        if not query:
            return _text_result("query 不能为空", True)
        limit = int(args.get("limit") or 10)
        hits = self.index.search(query, limit=min(limit, 50), under=args.get("folder"))
        if not hits:
            return _text_result(f"没有找到包含「{query}」的内容。{hint_if_building()}")
        lines = [f"找到 {len(hits)} 条（query={query}）："]
        for hit in hits:
            lines.append(f"\n{hit.path}  [{hit.loc}]\n  {hit.snippet}")
        return _text_result("\n".join(lines))

    def _tool_list(self, args: dict) -> dict:
        rows = self.index.list_files(under=args.get("folder"))
        if not rows:
            return _text_result(f"索引为空，先用 index_folder 建索引。{hint_if_building()}")
        lines = [f"共 {len(rows)} 个文件："]
        lines += [f"{path}  ({blocks} blocks)" for path, blocks in rows]
        return _text_result("\n".join(lines))

    def _tool_index(self, args: dict) -> dict:
        target = args.get("path")
        if not target or not Path(target).exists():
            return _text_result(f"路径不存在：{target}", True)
        stats = self.index.index_paths([target])
        return _text_result(
            f"索引完成：扫描 {stats['total']}，更新 {stats['indexed']}，"
            f"跳过 {stats['skipped']}，失败 {stats['failed']}，新增文本块 {stats['blocks']}。"
        )

    # ------------------------------------------------------------ dispatch

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            return _ok(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "officegrep", "version": "0.1.0"},
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return _ok(msg_id, {})
        if method == "tools/list":
            return _ok(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            handlers = {
                "search_docs": self._tool_search,
                "list_docs": self._tool_list,
                "index_folder": self._tool_index,
            }
            handler = handlers.get(name)
            if handler is None:
                return _err(msg_id, -32602, f"未知工具：{name}")
            try:
                return _ok(msg_id, handler(args))
            except Exception as exc:
                return _ok(msg_id, _text_result(f"工具执行失败：{exc}", True))
        if msg_id is None:
            return None
        return _err(msg_id, -32601, f"不支持的方法：{method}")

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            response = self.handle(msg)
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                stdout.flush()
