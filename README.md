# AI Research Assistant — Backend

Backend service for the AI Research Assistant. Searches ArXiv for academic papers, builds a persistent knowledge base with semantic embeddings in Supabase pgvector, and uses a multi-agent LLM pipeline to synthesise structured research reports. Optional PageIndex hybrid retrieval adds tree-based reasoning for deeper document understanding.

## Architecture

```
Request (topic)
    │
    ▼
┌──────────────────────────────────┐
│  FastAPI  (/api/research/dynamic)│
└──────────┬───────────────────────┘
           │
    ┌──────▼──────────┐  hit  ┌─────────────────────┐
    │ Supabase pgvector│──────►│ Return cached context│
    │ (cloud)          │       └──────────┬───────────┘
    └──────┬──────────┘                   │
           │ miss                         │
    ┌──────▼──────┐                       │
    │ ArXiv Search│                       │
    │ + PDF Extract│                      │
    └──────┬──────┘                       │
           │ chunk + embed + upload       │
    ┌──────▼──────────┐                   │
    │ Supabase pgvector│◄─────────────────┘
    │ (updated)        │
    └──────┬──────────┘
           │ Stage 1: vector search
    ┌──────▼──────────────────────┐
    │  PageIndex (if enabled)     │
    │  Stage 2: tree reasoning    │
    │  + context fusion           │
    └──────┬──────────────────────┘
           │ enriched context
    ┌──────▼──────────────────────────────┐
    │        Multi-Agent Pipeline         │
    │                                     │
    │  1. Paper Analyzer  (sub_llm)       │
    │        ↓                            │
    │  2a. Synthesis Agent (main_llm)  ─┐ │
    │  2b. Diagram Agent   (sub_llm)   ─┤ │  ← parallel
    │        ↓                          │ │
    │     Merge results ◄───────────────┘ │
    └──────┬──────────────────────────────┘
           │
    ┌──────▼──────┐
    │  Post-proc  │  Mermaid formatting, section visuals,
    │  + Visuals  │  math rendering, chart generation
    └──────┬──────┘     (uploaded to Supabase Storage)
           │
           ▼
     JSON Response
```

### Research flow

1. **Similarity search first** — The Supabase pgvector store is queried before downloading anything. If enough relevant chunks already exist (>= 15 above similarity threshold), the ArXiv step is skipped entirely.
2. **ArXiv download + embed** — When new papers are needed, they are fetched from ArXiv, text/images extracted via PyMuPDF, images and PDFs uploaded to Supabase Storage, text chunked (1500 chars / 300 overlap) with rich metadata, and embeddings stored in Supabase pgvector. Papers already in the store (matched by `arxiv_id`) are deduplicated.
3. **Hybrid retrieval (optional)** — When `PAGEINDEX_ENABLED=true`, the top 3 papers from vector search undergo PageIndex tree-based reasoning. An LLM navigates the document's hierarchical structure to identify precisely relevant sections. Results are fused with vector chunks for enriched context.
4. **Multi-agent pipeline** — Three specialised agents replace a single monolithic LLM call:
   - **Paper Analyzer** (sub-model) extracts structured findings: key findings, methodologies, statistics, comparisons, timeline, applications, risks.
   - **Synthesis Agent** (main model) writes the narrative summary and structured sections, receiving the analyzer output as pre-extracted data.
   - **Diagram Agent** (sub-model) generates properly formatted Mermaid diagrams with strict syntax rules. Runs in parallel with the Synthesis Agent.
5. **Post-processing** — Mermaid diagrams are reformatted (multi-line, quoted labels, validated). LaTeX expressions are rendered to PNG and uploaded to Supabase Storage. Statistics and comparison charts are generated via matplotlib and uploaded.

### Key files

| File | Purpose |
|---|---|
| `src/ai_research_backend/api.py` | FastAPI app, endpoints, Mermaid formatting, job orchestration |
| `src/ai_research_backend/agents.py` | Multi-agent pipeline (Paper Analyzer, Synthesis, Diagram) |
| `src/ai_research_backend/rag.py` | Supabase pgvector, chunking, embedding, similarity search, deduplication |
| `src/ai_research_backend/storage.py` | Supabase Storage wrapper (upload, download, public URLs) |
| `src/ai_research_backend/tree_index.py` | PageIndex tree generation, caching, LLM tree search |
| `src/ai_research_backend/hybrid_retrieval.py` | Two-stage retrieval orchestrator (vector + tree + fusion) |
| `src/ai_research_backend/crew.py` | LLM configuration (main + sub-agent), CrewAI crew definition |
| `src/ai_research_backend/tools/arxiv_tool.py` | ArXiv search, PDF download, text/image extraction, Supabase upload |
| `src/ai_research_backend/section_visuals.py` | LaTeX rendering, statistics/comparison chart generation |
| `src/ai_research_backend/models.py` | Pydantic models for API requests/responses |
| `src/ai_research_backend/job_manager.py` | In-memory job status tracking, file-based result storage |
| `src/ai_research_backend/config/agents.yaml` | CrewAI agent role/goal/backstory definitions |
| `src/ai_research_backend/config/tasks.yaml` | CrewAI task descriptions |

## Installation

Requires Python >= 3.10, < 3.14. Uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
pip install uv
cd ai_research_backend
uv sync
```

### Supabase setup

Before running the server, create a Supabase project and run the SQL setup script:

```bash
# Copy the SQL from docs/supabase_setup.sql into the Supabase SQL Editor
# This creates the pgvector extension, paper_chunks table, match_chunks RPC, and storage bucket
```

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```env
# Required: API key for all /api/research and /api/research/dynamic endpoints.
# Send as header: Authorization: Bearer <key> or X-API-Key: <key>
API_KEY=your_secret_key

# Main LLM (used by Synthesis Agent and CrewAI crew)
OLLAMA_API_KEY=your_key_here
OLLAMA_API_BASE=https://ollama.com/v1
OLLAMA_MODEL=gpt-oss:120b-cloud

# Sub-agent LLM (Paper Analyzer + Diagram Agent)
# Leave OLLAMA_SUB_MODEL empty to use the main model for all agents
OLLAMA_SUB_MODEL=llama3.2:3b
OLLAMA_SUB_API_BASE=http://localhost:11434/v1
OLLAMA_SUB_API_KEY=ollama

# Supabase (required)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-service-role-key
SUPABASE_STORAGE_BUCKET=research-assets

# PageIndex hybrid retrieval (optional)
PAGEINDEX_ENABLED=false
PAGEINDEX_MODE=self_hosted
PAGEINDEX_MODEL=
PAGEINDEX_MAX_TREE_PAPERS=3
```

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | — | **Required.** Shared secret for research endpoints. Send as `Authorization: Bearer <key>` or `X-API-Key: <key>`. |
| `MAX_CONCURRENT_RESEARCH_JOBS` | `1` | Max number of pending/running jobs. New submissions get 503 "Server busy" when at capacity. |
| `RATE_LIMIT_PER_MINUTE` | `10` | Max requests per minute per client (IP) on research endpoints. Exceeding returns 429. |
| `OLLAMA_API_KEY` | — | API key for the main Ollama Cloud model |
| `OLLAMA_API_BASE` | `https://ollama.com/v1` | Base URL for the main model API |
| `OLLAMA_MODEL` | `gpt-oss:120b-cloud` | Main model identifier (synthesis + CrewAI) |
| `OLLAMA_SUB_MODEL` | *(empty = use main)* | Smaller model for Paper Analyzer and Diagram Agent |
| `OLLAMA_SUB_API_BASE` | `http://localhost:11434/v1` | Base URL for the sub-agent model (e.g. local Ollama) |
| `OLLAMA_SUB_API_KEY` | `ollama` | API key for the sub-agent model |
| `SUPABASE_URL` | — | **Required.** Supabase project URL |
| `SUPABASE_KEY` | — | **Required.** Supabase service-role key |
| `SUPABASE_STORAGE_BUCKET` | `research-assets` | Supabase Storage bucket name |
| `PAGEINDEX_ENABLED` | `false` | Enable PageIndex hybrid retrieval |
| `PAGEINDEX_MODE` | `self_hosted` | `self_hosted` (open-source + Ollama) or `cloud` (PageIndex Cloud API) |
| `PAGEINDEX_API_KEY` | — | Required only for `cloud` mode |
| `PAGEINDEX_MODEL` | *(uses OLLAMA_MODEL)* | Model for tree generation and tree search |
| `PAGEINDEX_MAX_TREE_PAPERS` | `3` | Max papers to tree-index per request |
| `TAVILY_API_KEY` | — | *(Optional)* Tavily web search API key |

### Using a local Ollama model for sub-agents

To run Paper Analyzer and Diagram Agent on a local Ollama instance:

```bash
ollama pull llama3.2:3b
```

Then set in `.env`:

```env
OLLAMA_SUB_MODEL=llama3.2:3b
OLLAMA_SUB_API_BASE=http://localhost:11434/v1
OLLAMA_SUB_API_KEY=ollama
```

## Running the project

### FastAPI server

```bash
uv run uvicorn src.ai_research_backend.api:app --host 0.0.0.0 --port 8000 --reload
```

Or via script entry point:

```bash
uv run run_api
```

### CrewAI crew (standalone)

```bash
crewai run
```

## API endpoints

All research endpoints require a valid API key: send `Authorization: Bearer <API_KEY>` or `X-API-Key: <API_KEY>`. The root `GET /` is unauthenticated. Rate limiting applies per client (by IP); exceeding the limit returns `429 Too Many Requests`. If the server already has the maximum number of research jobs running or pending, new submissions receive `503 Service Unavailable` with `"code": "SERVER_BUSY"`.

### Dynamic research (multi-agent pipeline)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/research/dynamic` | Submit a dynamic research job |
| `GET` | `/api/research/dynamic/{job_id}/result` | Get structured research result |

The dynamic result includes: `summary`, `key_insights`, `generated_diagrams` (Mermaid), `structured_sections`, `section_confidence`, `section_images`, and `papers`.

### CrewAI research (agent crew)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/research` | Submit a CrewAI research job |
| `GET` | `/api/research/{job_id}` | Get job status + progress |
| `GET` | `/api/research/{job_id}/result` | Get markdown report + sources |

### Other

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Health check / root |

CORS is configured for `http://localhost:5173` and `*.slickspender.com`.

## Data storage

All persistent data is stored in Supabase (cloud). The server is stateless.

| Location | Description |
|---|---|
| Supabase pgvector (`paper_chunks` table) | Paper chunk embeddings and metadata |
| Supabase Storage `extracted_images/` | Images extracted from paper PDFs |
| Supabase Storage `generated_math/` | Rendered LaTeX equation PNGs |
| Supabase Storage `generated_charts/` | Matplotlib bar/comparison charts |
| Supabase Storage `pdfs/` | Paper PDFs (for PageIndex tree generation) |
| Supabase Storage `tree_indexes/` | Cached PageIndex tree structures (JSON) |
| Local `results/` | Job result JSON files (in-memory + file backup, evicted after 2 hours) |

## Chunking and embedding strategy

Papers are split into two chunk types:

- **Abstract chunks** — The paper abstract as a single high-signal chunk (`chunk_type: "abstract"`).
- **Body chunks** — Full paper text cleaned (unicode normalised, citation brackets removed, email/URL stripped) then split at 1500 characters with 300-character overlap (`chunk_type: "body"`).

Each chunk carries rich metadata: `title`, `authors`, `published`, `arxiv_id`, `pdf_url`, `chunk_type`, `chunk_position` (start/middle/end), `chunk_index`, `total_chunks`, and `topic_query`.

Embeddings are generated using `all-MiniLM-L6-v2` (384 dimensions) via sentence-transformers and stored in Supabase pgvector with IVFFlat indexing.

Deduplication is by `arxiv_id` — if a paper is already in the vector store, its chunks are not re-added.

## Hybrid retrieval (PageIndex)

When `PAGEINDEX_ENABLED=true`, retrieval uses a two-stage pipeline:

1. **Stage 1 (vector search)** — Supabase pgvector identifies the most semantically similar chunks and ranks papers by average similarity score.
2. **Stage 2 (tree reasoning)** — The top 3 papers undergo PageIndex tree generation. An LLM navigates the document's hierarchical structure to identify precisely relevant sections, extracting text from targeted page ranges.
3. **Context fusion** — Vector search chunks (broad coverage) and tree-retrieved sections (precise relevance) are merged with source attribution (`[vector-search]` vs `[tree-search]`) and passed to the multi-agent pipeline.

Tree indexes are cached in Supabase Storage, so subsequent queries for the same paper skip tree generation.
