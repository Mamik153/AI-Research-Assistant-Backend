# Changelog

All notable changes to this project will be documented in this file.

Versions follow [Semantic Versioning](https://semver.org/).

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
