# Changelog

All notable changes to this project will be documented in this file.

Versions follow [Semantic Versioning](https://semver.org/).

## [0.6.2] - 2026-02-28

### Fix
- **Container OOM kill during research jobs:** The `crewai` package pulls in `chromadb` and `onnxruntime` as core transitive dependencies. ChromaDB's default embedding function downloads and loads a ~79 MB ONNX model at runtime, causing the container to exceed its memory limit and get killed. Decoupled the dynamic research path from crewai by introducing `llm_config.py`, a lightweight LLM wrapper using `litellm` directly. The module-level import in `api.py` no longer triggers the crewai -> chromadb -> onnxruntime import chain, saving ~200-400 MB of peak memory.

### Minor change
- Pre-download the `all-MiniLM-L6-v2` sentence-transformers embedding model at Docker build time so it is baked into the image layer instead of downloaded on first request.
- Removed legacy `chroma_db/` directory and outdated ChromaDB volume instructions from Railway deployment docs (ChromaDB was replaced by Supabase pgvector in v0.6.0).

## [0.6.1] - 2026-02-28

### Fix
- **Stuck research jobs:** Background research jobs could hang permanently at 5% "Initializing research" due to heavy deferred imports (`transformers`, `crewai`) inside the background task. A prior `OSError: [Errno 89]` during `transformers` import left partial modules in `sys.modules`, causing subsequent jobs to deadlock on the import lock. Moved all heavy imports to module-level with isolated try/except fallbacks so failures surface at startup instead of silently hanging background tasks.
- **Missing error handling in vector search:** `_similarity_search` and `search_existing_knowledge` in `rag.py` had no try/except — a Supabase RPC hang or embedding failure would block the entire job indefinitely. Both now catch exceptions and return empty results, allowing the pipeline to fall back to ArXiv paper download.
- **No job timeout:** Added a configurable watchdog timer (`JOB_TIMEOUT_SECONDS`, default 600s) that marks stuck jobs as `"failed"` if they exceed the deadline. Previously a hung job would stay in `"running"` state forever, also blocking new submissions when `MAX_CONCURRENT_RESEARCH_JOBS=1`.

### Minor change
- Added per-stage try/except in `run_dynamic_research_job` with granular progress updates so each phase (knowledge-base search, ArXiv download, hybrid retrieval, agent pipeline) fails independently and reports which step failed.
- Repaired venv package metadata (`uv sync`) to resolve `OSError: [Errno 89] Operation canceled` during `importlib.metadata.packages_distributions()`.

## [0.6.0] - 2026-02-28

### Fix
- **Mermaid diagram generation:** Diagram Agent prompt now enforces valid syntax: flowcharts/graphs must use `NodeID["Label"]` (no bare quoted strings as nodes), and sequence diagrams must use `participant Id as "Label"` with only `Id` in arrows (no bracket syntax like `Actor["User Query"]`). Added post-processing in `_format_mermaid_diagram` to repair bare-quoted nodes in flowchart/graph by rewriting them to `Id["Label"]` form; labels inside existing brackets are left unchanged. Fixes frontend renderer failures for generated diagrams.

### BREAKING CHANGE
- Replaced local ChromaDB vector store with **Supabase pgvector**. The `chroma_db/` directory is no longer created or used. Requires `SUPABASE_URL` and `SUPABASE_KEY` environment variables.
- Removed the `/static/{file_path}` endpoint. All images (extracted paper figures, rendered LaTeX, generated charts) are now served directly from **Supabase Storage** CDN URLs. Clients no longer need to authenticate static file requests separately.
- Removed `API_BASE_URL` environment variable (no longer needed; image URLs are absolute Supabase Storage URLs).

### Major change
- **Supabase cloud migration:** All persistent storage moved to Supabase — pgvector for chunk embeddings/metadata and Storage for images, PDFs, and tree caches. The server is now fully stateless (no local disk writes except short-lived job result JSON).
- **PageIndex hybrid retrieval:** New two-stage retrieval pipeline behind `PAGEINDEX_ENABLED` feature flag. Stage 1 uses Supabase pgvector for broad vector similarity search. Stage 2 uses PageIndex tree-based reasoning to deeply analyse the top 3 papers, extracting precisely relevant sections via LLM-guided tree navigation. Tree indexes are cached in Supabase Storage for reuse.
- Embeddings are now generated explicitly using `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensions) — same model as ChromaDB's default, preserving retrieval behavior.

### Minor change
- Added new modules: `storage.py` (Supabase Storage wrapper), `tree_index.py` (PageIndex integration), `hybrid_retrieval.py` (two-stage retrieval orchestrator).
- ArXiv tool now uploads extracted images and PDFs to Supabase Storage instead of writing to local filesystem.
- Section visuals (math rendering, charts) now render to in-memory buffers and upload to Supabase Storage.
- Removed `delayed_cleanup()` function — no local files to clean up.
- Added `docs/supabase_setup.sql` with full database and storage bucket setup instructions.

## [0.5.1] - 2026-02-28

### Fix
- Relaxed Content-Security-Policy for `/docs` and `/redoc` so Swagger UI and ReDoc can load CDN assets (cdn.jsdelivr.net) and inline scripts; all other routes keep strict `default-src 'self'`.

## [0.5.0] - 2026-02-28

### BREAKING CHANGE
- Static files at `/static/` now require API key authentication. Clients must include `Authorization: Bearer <key>` or `X-API-Key: <key>` when fetching images and charts.
- CORS production regex now only allows HTTPS origins on `slickspender.com` (previously allowed HTTP). Allowed methods restricted to GET, POST, OPTIONS; allowed headers restricted to Authorization, X-API-Key, Content-Type.
- The `API_KEY not configured` response no longer returns HTTP 501; it now returns 401 (same as invalid key) to prevent configuration state disclosure.

### Major change
- **Security hardening:** Addressed 22 identified vulnerabilities across critical, high, medium, and low severity levels.
- API key comparison now uses constant-time `hmac.compare_digest()` to prevent timing side-channel attacks (CVE-2026-23996 pattern).
- Added `SecurityHeadersMiddleware` injecting X-Content-Type-Options, X-Frame-Options, Strict-Transport-Security, Content-Security-Policy, Referrer-Policy, and Permissions-Policy on all responses.
- Added `RequestSizeLimitMiddleware` rejecting request bodies larger than 1 MB (HTTP 413).
- All `job_id` path parameters are now validated as proper UUIDs before file-system access, preventing path traversal.
- Error messages returned to clients are now generic; detailed exception info is logged server-side only.
- Research topic input now enforces 3-500 character length and strips control characters to mitigate prompt injection.
- Dockerfile now runs as non-root `appuser` instead of root.
- In-memory job store now has TTL-based eviction (2 hours for terminal jobs, 500 job cap) to prevent unbounded memory growth.
- Minimum PyMuPDF version bumped to >=1.27.1 to address VU#504749 (path traversal) and CVE-2025-55780 (NULL pointer DoS).

## [0.4.0] - 2026-02-27

### Major change
- Research endpoints (`POST /api/research`, `POST /api/research/dynamic`, and their GET status/result routes) now return **503 Service Unavailable** with `"code": "SERVER_BUSY"` when there are already ongoing (pending or running) research jobs at or above the configured limit. This prevents overload and gives clients a clear signal to retry later.

### Minor change
- **Rate limiting:** All research API endpoints are rate-limited per client (by IP). Default 10 requests per minute; configurable via `RATE_LIMIT_PER_MINUTE`. Exceeding returns **429 Too Many Requests** with `"code": "RATE_LIMIT_EXCEEDED"`.
- **Simple authorization:** Research endpoints require a valid API key. Set `API_KEY` in the environment and send it as `Authorization: Bearer <key>` or `X-API-Key: <key>`. Missing or invalid key returns **401 Unauthorized**; if `API_KEY` is not set, the server returns **501 Not Implemented**. Root `GET /` remains unauthenticated.
- **Config:** `MAX_CONCURRENT_RESEARCH_JOBS` (default 1) controls how many jobs can be pending or running before new submissions are rejected with 503.

## [0.3.0] - 2026-02-26

### Major change
- Replaced single monolithic LLM call with a 3-agent pipeline: Paper Analyzer (extracts structured findings), Synthesis Agent (writes narrative + structured sections), and Diagram Agent (generates Mermaid diagrams). Synthesis and Diagram agents run in parallel for faster results.
- Added persistent vector store (ChromaDB PersistentClient) with similarity-search-first logic. Repeated queries on the same topic now reuse existing embeddings instead of re-downloading papers from ArXiv.
- Improved chunking strategy: larger chunks (1500 chars / 300 overlap), rich metadata (arxiv_id, chunk_type, chunk_position, pdf_url, topic_query), separate abstract embeddings, text cleaning, and deduplication by arxiv_id.

### Fix
- Fixed case-sensitivity bug in Mermaid diagram validation that silently rejected `sequenceDiagram`, `classDiagram`, `stateDiagram`, and `erDiagram` diagram types.
- Added `_format_mermaid_diagram()` post-processor that converts semicolon-separated single-line diagrams to proper multi-line format and escapes special characters in node labels for reliable frontend parsing.
- Updated LLM prompts to produce multi-line Mermaid syntax with explicit formatting rules and examples.

### Minor change
- Added configurable sub-agent LLM via `OLLAMA_SUB_MODEL`, `OLLAMA_SUB_API_BASE`, `OLLAMA_SUB_API_KEY` environment variables for running smaller Ollama models on analysis/diagram tasks.
- Added `arxiv_id` field to paper data extracted from ArXiv for deduplication and tracking.
- Fixed duplicate except block in ArXiv search tool.

## [0.2.0] - 2026-02-26

### Minor change
- Added `section_confidence` to the dynamic research result JSON. Each of the 10 structured section keys (overview, key_concepts, benefits, risks, applications, future_directions, methodologies, comparisons, timeline, statistics) receives a confidence score between 0.0 and 1.0 indicating how well the section is supported by the retrieved papers.
- Added `section_images` to the dynamic research result JSON. Each section key maps to a list of image URLs — extracted paper figures assigned by the LLM, rendered LaTeX equations, and auto-generated data charts (statistics bar chart, comparison grouped bar chart).
- All image URLs in the API response (`papers[].images`, `section_images`) are now returned as absolute URLs using the configurable `API_BASE_URL` environment variable (defaults to `http://localhost:8000`).
- CORS now allows requests from any subdomain of `slickspender.com` in addition to `localhost:5173`.
- Added `matplotlib` dependency for chart and math rendering.

## [0.1.0] - Initial release

### Minor change
- Initial project setup with CrewAI-powered research agents, FastAPI server, ArXiv paper search, RAG-based paper chunking, and structured section output.
