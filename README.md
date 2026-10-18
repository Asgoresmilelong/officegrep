# officegrep

[![ci](https://github.com/Asgoresmilelong/officegrep/actions/workflows/ci.yml/badge.svg)](https://github.com/Asgoresmilelong/officegrep/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)](https://modelcontextprotocol.io)

**Full-text search inside Word, PowerPoint, Excel and PDF — for agents.**

`grep` cannot see a single character inside a `.docx`. `officegrep` extracts the text, indexes it into one SQLite file, and hands it to any agent over MCP.

```
$ officegrep search "bending moment"

Found 3 hits:

F:\reports\pole-design.docx  [table p 89]
  design bending moment (load point 0.25 m from tip); deflection limit; test moment = 2× …

F:\notes\composites.md  [line 32]
  … utilization 81%. **test bending moment = 2× the standard test moment** …
```

中文说明：[README.zh-CN.md](README.zh-CN.md)

## Why

- `grep` / `ripgrep` only read plain text. `.docx` is a zip of XML, `.xlsx` keeps strings in a shared table, `.pdf` is a compressed stream.
- Generic RAG needs an embedding service, a vector store, and a network round-trip.
- This needs one SQLite file: offline, incremental, milliseconds.

## Install

```bash
pip install git+https://github.com/Asgoresmilelong/officegrep
# optional PDF support
pip install "officegrep[pdf] @ git+https://github.com/Asgoresmilelong/officegrep"
```

Runtime deps: `lxml`, `openpyxl`. PDF needs optional `pypdf`.

## CLI

```bash
officegrep index ~/Documents ~/Downloads     # build / update (incremental)
officegrep search "island reef" --limit 5
officegrep search "wind load" --under ~/Documents
officegrep stats
```

The index lives in `~/.officegrep/index.db`; override with `--db` or `OFFICEGREP_DB`.

## MCP server

```bash
officegrep mcp
```

Client config (Claude Desktop / Cursor / Codex / any MCP host):

```json
{
  "mcpServers": {
    "officegrep": {
      "command": "officegrep",
      "args": ["mcp", "--db", "/Users/me/.officegrep/index.db"]
    }
  }
}
```

| Tool | What it does |
|---|---|
| `search_docs` | Full-text search → file path, locator (`p 42`, `slide 3`, `Sheet1!12`, `page 7`), highlighted snippet |
| `list_docs` | Indexed files with block counts |
| `index_folder` | Build / refresh the index (incremental) |

The protocol is a hand-written JSON-RPC over stdio — no MCP SDK dependency.

## How it works

```
docx / pptx  →  zipfile + lxml, read the XML directly
xlsx         →  openpyxl (read_only)
pdf          →  pypdf (optional)
txt / md     →  line chunks, line numbers kept
```

- Every block carries a **human-readable locator**, because the first question an agent asks is "where?".
- The index uses SQLite **FTS5 with the `trigram` tokenizer**: substring matching for both CJK and Latin text, no word segmenter required, ranked by `bm25()`.
- Queries of 1–2 characters cannot match trigrams, so they fall back to a scan ranked by hit count (CJK queries are frequently two characters).
- Incremental: compare `(size, mtime)` first, hash with SHA-1 only when they differ.

Benchmark on a real engineering corpus:

| Metric | Value |
|---|---|
| Files | 43 (docx / pptx / xlsx / md, incl. an 8.6 MB deck) |
| Text blocks | 2,260 |
| First index | 2.16 s |
| Database | 1.9 MiB |

## Limits

- No OCR: scanned PDFs yield nothing.
- Literal search, not semantic — "tower" will not find "pole" unless both appear.
- 1–2 character queries scan the table; slow past a few hundred thousand blocks.
- Indexes body text only: no comments, tracked changes, headers or footers.

## License

MIT
