# officegrep

**让 agent 搜得进 Word / PPT / Excel / PDF。** grep 看不见这些文件里的字，`officegrep` 把它们抽成文本块、建本地 SQLite 全文索引，再通过 MCP 交给任意 agent。

```
$ officegrep search "弯矩"

找到 3 条：

F:\报告\复合材料电线杆.docx  [table p 89]
  检验[弯矩]定义（荷载点距梢端 0.25 m）；挠度限值；承载力检验弯矩 = 2 倍标准检验弯矩 …

F:\notes\fuhe-pole.md  [line 32]
  …1618 mm 仍能过关（利用率 81%）。**承载力检验[弯矩] = 2×标准检验弯矩** …
```

## 为什么

- `grep` / `ripgrep` 只能读纯文本，`.docx` 是 zip+XML，`.xlsx` 是共享字符串表，`.pdf` 是压缩流；
- 通用 RAG 需要 embedding 服务、向量库、联网，重；
- 这个只需要一个 SQLite 文件：离线、增量、毫秒级。

## 安装

```bash
pip install git+https://github.com/Asgoresmilelong/officegrep
# PDF 支持（可选）
pip install "git+https://github.com/Asgoresmilelong/officegrep#egg=officegrep[pdf]"
```

只依赖 `lxml` 和 `openpyxl`；PDF 需要可选的 `pypdf`。

## 用法

```bash
officegrep index ~/Documents ~/Downloads        # 建/更新索引（增量）
officegrep search "岛礁" --limit 5               # 检索
officegrep search "风荷载" --under ~/Documents   # 限定目录
officegrep stats                                 # 索引统计
```

索引默认存在 `~/.officegrep/index.db`，可用 `--db` 或环境变量 `OFFICEGREP_DB` 改。

## 接 agent（MCP）

```bash
officegrep mcp
```

Claude Desktop / Cursor / Codex 等客户端配置：

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

三个工具：

| 工具 | 用途 |
|---|---|
| `search_docs` | 全文检索，返回路径 + 定位符（`p 42` / `slide 3` / `材料!12` / `page 7`）+ 片段 |
| `list_docs` | 列出已索引文件与文本块数 |
| `index_folder` | 建/更新索引（增量） |

协议实现是手写的 JSON-RPC over stdio，零 SDK 依赖。

## 实现

```
docx/pptx  →  zipfile + lxml 直接读 XML（段落、幻灯片、表格）
xlsx       →  openpyxl（read_only）
pdf        →  pypdf（可选）
txt/md     →  按行分块，保留行号
```

- 每个块带**人类可读定位符**，因为 agent 拿到结果后的第一个问题是「在哪儿」；
- 索引用 SQLite **FTS5 `trigram`** tokenizer：中英文都能子串匹配，不依赖分词器；
- 查询 ≥3 字符走 FTS5 + `bm25()` 排序；1–2 字符（中文最常见）FTS5 无法命中，回退到扫描并按**命中次数**排序；
- 增量：先比 `(size, mtime)`，不同再算 SHA-1，避免重复解析。

实测（一份真实工程语料）：

| 指标 | 值 |
|---|---|
| 文件 | 43 个（docx / pptx / xlsx / md，含 8.6 MB 的 PPT） |
| 文本块 | 2260 |
| 首次索引 | 2.16 s |
| 数据库 | 1.9 MiB |

## 局限

- 不解析扫描版 PDF（需要 OCR）；
- 是**字面检索**，不是语义检索——"杆塔"搜不到"电杆"，除非两者都出现；
- 1–2 字查询走全表扫描，几十万块以上会明显变慢；
- 只读文件内容，不索引批注、修订记录、页眉页脚。

## License

MIT
