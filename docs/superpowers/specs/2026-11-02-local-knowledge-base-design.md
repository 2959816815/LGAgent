# Local Knowledge Base Design

Date: 2026-11-02
Status: approved for implementation planning

## Background

The desktop agent can read individual files with `read_file`, `read_pdf`,
`read_docx`, `read_xlsx`, and `read_pptx`, but it has no persistent index. Every
question about a folder of notes or documents requires the model to discover and
re-read files, and the agent cannot search across a personal corpus.

Mainstream desktop agents provide a "project knowledge" or "local knowledge
base" capability: the user adds folders, the app indexes them in the background,
and the agent retrieves relevant passages automatically. This design adds that
capability to the LangGraph desktop agent. It is entirely local: extraction,
chunking, embedding, storage, and retrieval all run on the user's machine.

User decisions:

- One global knowledge base, not multiple named libraries.
- Agent decides when to search; a global on/off switch exists in settings.
- Expected scale: a few thousand files, up to tens of thousands of chunks.
- Backend: SQLite plus numpy brute-force vectors, FTS5 plus jieba for lexical
  search, reciprocal rank fusion. No Milvus, Chroma, sqlite-vec, or reranker.
- This phase writes the design only. No implementation, packaging, or
  installed-application changes yet.

## Goals

1. Let the user add one or more local folders in settings and index them.
2. Support `.txt`, `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.csv`, and
   `.json` with the libraries already used by the desktop sidecar.
3. Index incrementally: unchanged files are skipped; changed files replace their
   chunks atomically; deleted files are removed.
4. Embed locally through Ollama `qwen3-embedding:0.6b`; never call a cloud
   embedding service.
5. Retrieve with hybrid vector and lexical search fused by reciprocal rank
   fusion, and return source paths and locations.
6. Expose a `search_knowledge_base` tool that the agent calls automatically
   when the user asks about their own documents, notes, or previously discussed
   material.
7. Provide a clear settings UI: enable/disable, add/remove folders, indexing
   progress, cancel, and rebuild.
8. Keep the installer impact small: only `numpy` and `jieba` are added.

## Non-Goals

- Multiple named knowledge libraries, per-session toggles, or knowledge
  libraries shared between machines.
- A filesystem watcher. Startup incremental scans plus a manual update button
  are enough for this phase.
- OCR for scanned PDFs. Files with no extractable text are marked as empty.
- Complex table reconstruction, formulas, comments, or embedded objects.
- Cloud embedding fallback, reranking models, or code-aware chunking.
- A dedicated knowledge-browser panel in the chat UI. Settings plus the
  existing tool cards are enough.
- Packaging or installing the application.

## Architecture

```
Settings: add folders, enable/disable
              │
              ▼
KnowledgeIndexer (background daemon thread)
  scan → extract → chunk → embed (Ollama) → SQLite transaction
              │
              ▼
knowledge.db
  sources / documents / chunks / chunks_fts / settings
              │
              ▼
search_knowledge_base tool (agent auto-calls)
  vector top-20 + FTS5 top-20 → RRF → top-5..8 with sources
```

One process, one SQLite database, one background indexing thread, and an
in-process vector cache. Retrieval reads from the same process.

## Data Model

Database: `DSH_DATA_DIR/knowledge.db`, SQLite in WAL mode, foreign keys on.

```sql
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL UNIQUE,
    enabled      INTEGER NOT NULL DEFAULT 1,
    added_at     TEXT NOT NULL,
    last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    content_hash TEXT NOT NULL,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'indexed',
    error       TEXT,
    indexed_at  TEXT,
    UNIQUE(source_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    text            TEXT NOT NULL,
    heading         TEXT,
    page            INTEGER,
    sheet           TEXT,
    slide           INTEGER,
    tokens          INTEGER NOT NULL,
    embedding       BLOB NOT NULL,
    dim             INTEGER NOT NULL,
    embedding_model TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id    UNINDEXED,
    document_id UNINDEXED,
    tokens_text,
    tokenize = 'unicode61'
);
```

`settings` rows:

- `schema_version` = `1`
- `enabled` = `"1"` or `"0"`, default `"1"`
- `embedding_model` = `qwen3-embedding:0.6b`
- `embedding_dim` = observed at first successful embed, default `1024`
- `chunk_size` = `600`
- `chunk_overlap` = `100`
- `last_index_at` = ISO timestamp
- `index_version` = monotonically increasing integer; used to invalidate the
  vector cache

`documents.status` is one of `indexed` (searchable), `empty` (no extractable
text), or `error` (extraction or embedding failed). Unsupported and oversized
files are skipped during discovery and do not get a document row. Only
`indexed` documents participate in search.

`tokens_text` is the chunk text tokenized by `jieba.cut_for_search` and joined
with spaces. Storing the tokenized form is required because FTS5's `unicode61`
tokenizer treats a whole Chinese sentence as one token. The original text stays
in `chunks.text`.

Deleting a source deletes its documents (foreign key cascade) and the matching
`chunks_fts` rows explicitly. A document update runs in one transaction:
delete old chunks and FTS rows, insert new chunks and FTS rows, update the
document row.

## File Discovery

A source is an absolute directory chosen by the user through the Electron
folder picker.

Validation:

- The path must exist and be a directory.
- The resolved path is stored.
- A source cannot be nested inside another source and cannot contain an
  existing source. Reject with a clear message to avoid double indexing.
- Symbolic links are not followed for directories or files.

Walking:

- Recursively walk with `os.walk(..., followlinks=False)`.
- Skip directory names starting with `.`; skip these names case-insensitively:
  `node_modules`, `.git`, `.venv`, `venv`, `__pycache__`, `dist`, `build`,
  `.next`, `.cache`, `target`.
- Skip files starting with `.`, files larger than 20 MB, files containing a NUL
  byte in the first 8 KB, and unsupported extensions.
- Supported extensions, case-insensitive: `.txt .md .markdown .pdf .docx .xlsx
  .pptx .csv .json`.
- Never index `knowledge.db`, `agent.db`, or temporary files (`*.tmp`,
  `*.part`).
- The scan reports counts: files seen, skipped, new, changed, deleted,
  unchanged.

Change detection:

- Compare `size` and `mtime` first. If both are unchanged, skip without reading
  the file.
- If either changed, compute a SHA-256 content hash. If the hash matches the
  stored one, update the stored `size`/`mtime` and skip reindexing.
- Missing files are removed from `documents`, `chunks`, and `chunks_fts`.

## Text Extraction

Extraction returns a list of units:

```python
@dataclass(frozen=True)
class TextUnit:
    text: str
    heading: str = ""
    page: int | None = None
    sheet: str = ""
    slide: int | None = None
```

Per format:

| Format | Library | Location metadata | Limits |
| --- | --- | --- | --- |
| `.txt` | built-in | none | 2,000,000 chars |
| `.md` | built-in | heading path | 2,000,000 chars |
| `.pdf` | `pypdf` | page number | 2,000,000 chars |
| `.docx` | `python-docx` | heading style path | 2,000,000 chars |
| `.xlsx` | `openpyxl` (read-only) | sheet name | 2,000 rows/sheet, 50,000 chars/sheet |
| `.pptx` | `python-pptx` | slide number | 2,000,000 chars |
| `.csv` | built-in `csv` | none | 5,000 rows |
| `.json` | built-in `json` | none | 2,000,000 chars |

Unit boundaries:

- Each PDF page, PPTX slide, XLSX sheet, and Markdown heading section is one
  `TextUnit`.
- TXT, CSV, and JSON produce one `TextUnit` per file.
- DOCX produces one `TextUnit` per file; heading styles are recorded in the
  heading path.

Rules:

- Decode text with `utf-8-sig`, then `utf-8`, then `gb18030`, with replacement
  for undecodable bytes.
- For Markdown, track `#` headings and include the heading path in each unit.
  Do not interpret code fences differently; they are part of the text.
- For DOCX, also read table cells row by row (`cell | cell | ...`).
- For PDF, a document with fewer than 20 extracted characters in total is
  marked `status='empty'` with the message "没有可提取的文本，可能是扫描版 PDF".
- For JSON, pretty-print with `indent=2` before chunking.
- Any extractor exception marks the document `status='error'` and continues
  with the next file.

## Chunking

Constants:

- `CHUNK_SIZE = 600` characters
- `CHUNK_OVERLAP = 100` characters
- `MIN_CHUNK = 50` characters
- `MAX_CHUNK = 1000` characters

Algorithm:

1. Process each `TextUnit` separately; never merge across page, sheet, slide, or
   file boundaries.
2. Split the unit on blank lines into paragraphs.
3. If a paragraph is longer than `MAX_CHUNK`, split on sentence punctuation
   (`.。！？；!?;\n`) near the limit; hard-split if no punctuation is found.
4. Accumulate paragraphs into chunks up to `CHUNK_SIZE`. When starting a new
   chunk inside the same unit, prepend the last `CHUNK_OVERLAP` characters of
   the previous chunk.
5. Drop chunks shorter than `MIN_CHUNK` after stripping.
6. The embedding input is the location prefix plus the chunk text:
   `"[标题] 项目方案\n[第2页]\n<text>"`. The prefix is built from the unit
   metadata; empty parts are omitted.
7. Store the raw chunk text in `chunks.text`, the prefix-derived metadata in
   the dedicated columns, and the estimated token count in `chunks.tokens`.
   The token estimator is the CJK-aware estimator used by the context builder.

## Embedding

Endpoint: the local Ollama native API.

```text
POST http://127.0.0.1:11434/api/embed
{"model": "qwen3-embedding:0.6b", "input": ["...", "..."]}
```

- Derive the base URL from `OLLAMA_BASE_URL` by removing a trailing `/v1`; the
  default is `http://127.0.0.1:11434`.
- Batch size 16; request timeout 120 seconds; one retry on transport errors.
- Vectors are L2-normalized and stored as little-endian `float32` blobs.
- The first successful response sets `embedding_dim`; later responses with a
  different dimension mark the run as failed instead of mixing dimensions.
- Availability is checked with `GET /api/tags` (model present) before indexing
  and before a search that needs a query embedding. The status endpoint reports
  `embedding_available` and a reason.
- If Ollama is unreachable, indexing stops with a clear error and keeps all
  already-indexed documents. No cloud fallback is attempted.
- If the configured base URL host is not `127.0.0.1`, `localhost`, or `::1`, the
  status includes a privacy warning that embeddings leave the machine.

## Search

`search(query, top_k=5)`:

1. Clamp `top_k` to 1-10.
2. Try to embed the query through Ollama. If that fails, continue with lexical
   search only and add a warning to the result.
3. Vector recall:
   - Load all chunk vectors for documents whose source is enabled and whose
     status is `indexed`.
   - Build a `float32` matrix and an id array, cached by `index_version`.
   - Normalize the query vector, compute `matrix @ query`, take the top 20.
   - If fewer than 20 rows exist, use all of them.
4. Lexical recall:
   - Tokenize the query with `jieba.cut_for_search`.
   - Build an FTS5 query of the form `"token1" OR "token2" ...`, doubling any
     embedded double quotes.
   - Select the top 20 by `bm25(chunks_fts)`.
   - If tokenization produces no tokens, fall back to a `LIKE` scan over the
     chunk text, limited to 20.
5. Reciprocal rank fusion with `k = 60`: for each chunk id, sum
   `1 / (60 + rank)` over the lists in which it appears. Sort descending.
6. Take `top_k` chunks, join with document metadata, and return:

```python
{
    "query": "...",
    "enabled": True,
    "embedding_used": True,
    "warning": "",
    "results": [
        {
            "chunk_id": 123,
            "path": "项目A/方案.md",
            "absolute_path": "D:/资料/项目A/方案.md",
            "heading": "架构",
            "page": None,
            "sheet": "",
            "slide": None,
            "score": 0.031,
            "text": "...",
        }
    ],
}
```

- `path` is relative to the source root; `absolute_path` is included for the
  agent but is not shown in the normal UI by default.
- Adjacent chunks from the same document are not merged in this phase.
- If the knowledge base is disabled, has no sources, or has no indexed chunks,
  the search returns an empty result with a specific `reason`.

## Agent Integration

New factory in `knowledge_tools.py`:

```python
def build_knowledge_tools(knowledge) -> list:
    ...  # returns StructuredTool(name="search_knowledge_base", ...)
```

Tool signature:

```python
search_knowledge_base(query: str, top_k: int = 5) -> str
```

Output format:

```text
[知识库结果]
1. 来源: 项目A/方案.md（D:/资料/项目A/方案.md）· 标题: 架构 · 相关度 0.031
   正文...
2. 来源: 笔记/会议.md（D:/资料/笔记/会议.md）· 第3页 · 相关度 0.028
   正文...
```

No results:

```text
[知识库结果] 未找到相关内容。可以在设置 → 知识库中添加文件夹或更新索引。
```

Disabled:

```text
[知识库结果] 知识库未启用。请在设置 → 知识库中打开。
```

Wiring:

- `desktop_api` creates one `KnowledgeStore/KnowledgeIndexer/KnowledgeSearch`
  instance per app, bound to `DSH_DATA_DIR`.
- The tool is passed as an extra tool to `build_graph`, like MCP tools.
- `graph_agent.collect_tools` currently ignores all extra tools for local
  profiles. Change it so core mode keeps extra tools whose names are in
  `CORE_TOOL_NAMES`.
- Add `search_knowledge_base` to `CORE_TOOL_NAMES`, so local fallback and
  offline sessions can still search.
- The tool checks `enabled` at call time; it does not require rebuilding the
  graph when the switch changes.
- The tool schema adds roughly 200-300 tokens to the core tool set. The local
  16k window still fits its 8.5k conversation budget.

System prompt addition (concise, to protect the local window):

```text
当用户询问自己的资料、文档、笔记、之前说过的内容，或明确提到某个文件/主题
可能收在本地知识库时，先用 search_knowledge_base 检索；普通常识问题不要调用。
检索结果不足时如实说明，可以建议用户在设置 → 知识库中添加文件夹。
使用知识库内容回答时，在相应句子后标注“来源: 路径”。
```

## Indexing Lifecycle

`KnowledgeIndexer`:

- One daemon thread per run; only one run at a time.
- `start_scan(rebuild=False)`:
  - If a run is active, return 409 from the API.
  - If `rebuild`, delete all `documents`, `chunks`, and `chunks_fts` rows while
    keeping sources and settings.
  - Spawn the worker and return immediately.
- State machine: `idle`, `scanning`, `extracting`, `embedding`, `done`,
  `error`, `cancelled`.
- Progress fields: `state`, `total_files`, `processed_files`,
  `indexed_documents`, `failed_documents`, `current_file`, `started_at`,
  `finished_at`, `message`.
- The worker checks a `threading.Event` between files and between embedding
  batches. Cancellation keeps completed documents and marks the run
  `cancelled`.
- Each document is committed atomically after all of its embeddings succeed.
  A failure leaves the previous version of that document intact and sets
  `status='error'` plus `error`.
- After a successful run, increment `index_version`, set `last_index_at`, and
  invalidate the vector cache.

Startup:

- Five seconds after app startup, if the feature is enabled and at least one
  source exists, start an incremental scan in a daemon thread.
- The startup scan does not rebuild; it only stats files and reindexes changes.
- If another run is active (for example, the user clicked update immediately),
  the startup task is skipped.
- `create_app` accepts `knowledge_autostart: bool = True`; tests pass `False`
  so background scans never interfere with test databases.

Capacity:

- Stop indexing when the chunk count would exceed 50,000.
- Mark the state `error` with "知识库已达 5 万段上限" and keep the existing
  index searchable.
- At 1024 dimensions, 50,000 float32 vectors consume about 205 MB.

## API

All endpoints require the existing desktop token.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/knowledge/status` | Full status and counts |
| POST | `/api/knowledge/sources` | Add a folder |
| DELETE | `/api/knowledge/sources/{id}` | Remove a folder and its index |
| POST | `/api/knowledge/index` | Start an incremental or full update |
| POST | `/api/knowledge/cancel` | Cancel the active run |
| PATCH | `/api/knowledge/settings` | Toggle `enabled` |
| GET | `/api/knowledge/search?q=...&top_k=...` | Search preview |

Status response:

```json
{
  "enabled": true,
  "indexing": false,
  "state": "idle",
  "progress": {
    "total_files": 120,
    "processed_files": 120,
    "indexed_documents": 118,
    "failed_documents": 2,
    "current_file": "",
    "started_at": "2026-11-02T10:00:00+08:00",
    "finished_at": "2026-11-02T10:00:42+08:00",
    "message": ""
  },
  "sources": [
    {"id": 1, "path": "D:/资料", "document_count": 120, "enabled": true}
  ],
  "documents": 120,
  "chunks": 8421,
  "last_index_at": "2026-11-02T10:00:42+08:00",
  "embedding": {
    "model": "qwen3-embedding:0.6b",
    "available": true,
    "reason": "",
    "warning": ""
  }
}
```

Source add validation:

- `path` must be an absolute existing directory.
- Reject nested or overlapping sources.
- Return 400 with a Chinese message for invalid input.
- On success, insert the source and start an incremental scan.

Index request:

```json
{"rebuild": false}
```

- Returns 409 when a run is active.
- Returns `{"started": true, "state": "scanning"}` immediately.

Settings request:

```json
{"enabled": false}
```

- Updates the setting and returns the status payload.

Search preview:

- Returns the same structure as the internal search result.
- Empty `q` returns 400.
- Available even when disabled for debugging; the response includes
  `"enabled": false` and an empty result with `reason="disabled"`.

## Frontend

Electron:

- Add `pickFolder()` to `preload.js` and an IPC handler in `main.js` that calls
  `dialog.showOpenDialog({ properties: ["openDirectory"] })`.
- Update the `window.desktop` type declaration used by the frontend.

Settings dialog:

- Turn the current model-only dialog into a two-tab dialog: `模型` and
  `知识库`. The model tab keeps the existing behavior.
- New `KnowledgePanel` component:
  - Enable switch.
  - Embedding status line (`qwen3-embedding:0.6b` available or reason).
  - "添加文件夹" button.
  - Source list with path, document count, remove button.
  - Index status: state, progress bar, current file, indexed/failed counts,
    last index time, error message.
  - Buttons: "更新索引", "重建索引" (confirm), "取消" (visible while running).
  - Poll `/api/knowledge/status` every 1 second while a run is active and every
    10 seconds otherwise.
- The panel shows the privacy note: "使用云端模型时，检索到的资料会作为上下文
  发送给云端模型；需要完全离线请切换到本地模型。"

Chat:

- No new panel. The existing tool card shows `search_knowledge_base` and its
  arguments/results.
- The tool result text already includes source paths, so the assistant can cite
  them.

## Dependencies and Packaging

Add to `requirements-desktop.txt`:

```text
numpy>=1.26
jieba>=0.42
```

- Both are pure-Python/wheel dependencies with no native database.
- PyInstaller must collect jieba's dictionary data; add a hook or
  `--collect-data jieba` in `packaging/build-sidecar.mjs` when packaging is
  eventually requested. Do not package in this phase.
- The embedding model is not bundled. It is installed in Ollama by the user:
  `ollama pull qwen3-embedding:0.6b`.
- The development `rag` environment already has both packages and the model.

## Error Handling

- Source path invalid or overlapping: 400 with a Chinese message.
- Indexing run already active: 409.
- Ollama unavailable: status shows `embedding.available=false` with a reason;
  indexing stops and reports the error.
- Embedding dimension mismatch: the run fails; nothing partially written for
  the current document.
- Extraction failure: document marked `error`; the run continues.
- Empty/scanned PDF: document marked `empty`; the run continues.
- Capacity exceeded: run stops with an error; the existing index remains usable.
- Search with no query embedding: lexical-only fallback with a warning.
- Search with no index: empty result with `reason="empty"`.
- Disabled: the tool returns the disabled message and search preview returns an
  empty result.
- App shutdown during indexing: per-document transactions keep the database
  consistent; the next startup scan resumes.

## Privacy and Security

- Only user-added folders are scanned. No home-directory or drive-wide scan.
- Symlinks are not followed; hidden and build directories are skipped.
- File contents are never logged.
- Embeddings go only to the configured Ollama endpoint. If it is not a
  loopback host, the status shows a warning.
- Retrieved chunk text is sent to the active chat model as part of the prompt.
  When the active model is cloud-hosted, the user must understand that local
  knowledge leaves the machine. The settings panel shows this warning.
- The knowledge database is local and separate from `agent.db`; deleting it
  only removes the index.

## Performance

- 20,000 chunks at 1024 dimensions: about 82 MB of vectors; vector search is a
  single matrix-vector product, typically under 100 ms.
- Lexical search is FTS5 and typically under 30 ms.
- Query embedding through local Ollama is the dominant cost, typically a few
  hundred milliseconds on CPU.
- The vector matrix and id array are cached by `index_version`. The first
  search after indexing rebuilds the cache in the query thread.
- Indexing throughput is dominated by embedding; batches of 16 keep Ollama
  responsive for chat.

## Testing

Unit tests:

- Chunking: paragraph boundaries, sentence splitting, overlap, minimum length,
  metadata propagation, no cross-unit merging.
- Extraction: txt/md/docx/xlsx/pptx/csv/json from generated temporary files;
  PDF via a small text fixture; empty PDF detection.
- Discovery: unsupported extensions, hidden/build directories, size limit, NUL
  byte detection, symlink skipping, nested/overlapping source rejection.
- Incremental scan: unchanged skip, mtime-only change with same hash, changed
  content, deleted file, removed source.
- FTS5 tokenization: Chinese terms are retrievable; quote escaping.
- RRF: ranks combine correctly; deduplication by chunk id.
- Vector search: normalization, top-k, dimension mismatch handling.
- Embedding client: batching, retry, dimension capture (fake HTTP server).
- Capacity cap at 50,000 chunks.

API tests:

- Status payload counts and embedding availability.
- Add/remove source validation and cascade cleanup.
- Index start/409/cancel/progress.
- Settings toggle.
- Search preview with a fake embedder.
- Tool output formatting for results, empty, disabled, and embedding failure.

Frontend tests:

- Settings tabs render.
- Add folder calls `pickFolder` and the API.
- Source list/remove.
- Progress polling and cancel.
- Embedding warning and privacy note.

Manual verification:

1. Add a folder with `.md`, `.pdf`, `.docx`, `.xlsx`, `.pptx`, and `.csv`.
2. Index and confirm counts and progress.
3. Ask a question whose answer exists only in that folder; confirm the tool is
   called and the answer cites the source path.
4. Modify one file and delete another; update the index; confirm the changed
   document is reindexed and the deleted one is gone.
5. Stop Ollama; confirm indexing/search report a clear error.
6. Disable the global switch; confirm the tool returns the disabled message.
7. Use only the local chat model and confirm retrieval works offline.

## Implementation Order

1. `knowledge_store.py`: schema, settings, sources, documents, chunks, FTS
   writes, incremental scan bookkeeping.
2. `knowledge_extract.py`: format extraction and chunking.
3. `knowledge_embed.py`: Ollama embed client, batching, availability.
4. `knowledge_search.py`: vector matrix, FTS query, RRF, result formatting.
5. `knowledge_indexer.py`: background run, progress, cancellation, capacity,
   startup scan.
6. `knowledge_tools.py` and backend API endpoints.
7. `graph_agent.collect_tools` core-extra whitelist and prompt rules.
8. Electron folder picker and the settings KnowledgePanel.
9. Tests and manual verification.

## Rollback

Delete `DSH_DATA_DIR/knowledge.db` to remove all indexed data. The knowledge
feature has no effect on sessions, models, MCP, or the existing file tools.
Implementation will stay in the development tree until the user asks for a
package.
