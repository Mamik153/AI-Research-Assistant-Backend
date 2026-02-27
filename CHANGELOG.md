# Changelog

All notable changes to this project will be documented in this file.

Versions follow [Semantic Versioning](https://semver.org/).

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
