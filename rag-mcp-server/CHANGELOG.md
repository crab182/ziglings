# Changelog

## Unreleased — Streaming, citations, Docling, context expansion, ops polish

### Added
- **Streaming Ask**: `POST /api/documents/ask/stream` (SSE: sources → delta → done);
  web UI streams answers live.
- **Inline clickable citations**: LLM emits `[n]` markers; UI renders them as
  superscripts that scroll to / highlight the matching source.
- **Document preview modal**: click any source or document name to view the full
  reconstructed document with query-term highlighting.
- **Docling PDF parsing** (always-on): layout/table-aware Markdown with page
  markers; falls back to pypdf. Structured chunker now runs on PDFs.
- **Parent-child context expansion**: prev/next chunks fed to the LLM (`EXPAND_CONTEXT`).
- **Contextual-retrieval ingest** (opt-in, `ENABLE_CONTEXTUAL_INGEST`): LLM-enriched
  chunks before embedding.
- **Upload progress**: per-file status (uploading % → processing → done/failed).
- **MCP discovery**: `/.well-known/mcp` server card; Dashboard shows live tool/capability counts.
- **Ollama auto-pull** + Docker **healthchecks** on all services; ordered startup.
- **Unraid notifications** (opt-in) on scheduled-sync complete/failed.
- **Eval harness** (`tests/test_eval.py`): functional in-memory store, top-1 ranking golden set.

### Changed
- `list_collections` now returns both `document_count` (unique sources) and `chunk_count`.
- Upload/reindex parsing offloaded to threads (Docling is CPU-heavy).
- Backend memory limit 4G → 6G (Docling models).

## PR #8 — Phase 3 & 4 + recheck

- MCP auto-auth via backend-provisioned service key (fixes 403s after first admin key).
- Event-loop offload for query/ask; HyDE circuit breaker; atomic config writes.
- Hybrid search (BM25+RRF), cross-encoder reranker, structured chunking,
  content-hash dedup, prompt-injection sanitization, Ollama Ask, metrics,
  MCP Resources + Prompts, dark/light theme, keyboard shortcuts.

## PR #5 — Serialization & startup hardening

- Fixed `jsonable_encoder` 500s; lazy imports; `/api/debug`; QA suite; GPU (cu128); local client.

## PR #1 — Initial RAG + MCP + SMB stack

- FastAPI + ChromaDB + sentence-transformers backend, MCP server (SSE + Streamable HTTP),
  React UI, SMB ingestion, HTTPS, auth tiers.
