"""Vector store operations backed by Supabase pgvector + sentence-transformers.

Chunks research papers, generates embeddings, and performs similarity search
against the ``paper_chunks`` table in Supabase.
"""

import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ai_research_backend.storage import get_supabase_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 300
TOP_K_CHUNKS = 25
TABLE_NAME = "paper_chunks"

SIMILARITY_THRESHOLD = 0.17
MIN_RELEVANT_CHUNKS = 15

# ---------------------------------------------------------------------------
# Embedding model (lazy singleton)
# ---------------------------------------------------------------------------

_embed_model = None


def _get_embed_model():
    """Load the all-MiniLM-L6-v2 sentence-transformer once."""
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer

        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Loaded embedding model all-MiniLM-L6-v2")
    return _embed_model


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Return 384-dim embedding vectors for a list of texts."""
    model = _get_embed_model()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def _clean_paper_text(text: str) -> str:
    """Clean raw PDF-extracted text before chunking."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", text)
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Chunking (unchanged logic, adjusted return type)
# ---------------------------------------------------------------------------


def chunk_papers(
    papers: List[dict],
    query: str = "",
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> List[dict]:
    """Split papers into overlapping text chunks with rich metadata.

    Returns a list of dicts ready for Supabase insertion, each with keys:
    ``id``, ``content``, and all metadata columns.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    rows: List[dict] = []

    for paper_idx, paper in enumerate(papers):
        arxiv_id = paper.get("arxiv_id", f"unknown_{paper_idx}")
        base_meta = {
            "title": paper.get("title", "Unknown"),
            "authors": ", ".join(paper.get("authors", [])),
            "published": paper.get("published", ""),
            "arxiv_id": arxiv_id,
            "pdf_url": paper.get("pdf_url", ""),
            "topic_query": query,
        }

        abstract = (paper.get("summary") or "").strip()
        if abstract:
            rows.append({
                "id": f"{arxiv_id}_abstract",
                "content": abstract,
                **base_meta,
                "chunk_type": "abstract",
                "chunk_position": "start",
                "chunk_index": 0,
                "total_chunks": 1,
            })

        content = paper.get("content") or ""
        cleaned = _clean_paper_text(content)
        if not cleaned:
            continue

        text_chunks = splitter.split_text(cleaned)
        total = len(text_chunks)
        for chunk_idx, chunk_text in enumerate(text_chunks):
            if chunk_idx < total * 0.2:
                position = "start"
            elif chunk_idx > total * 0.8:
                position = "end"
            else:
                position = "middle"

            rows.append({
                "id": f"{arxiv_id}_body_{chunk_idx}",
                "content": chunk_text,
                **base_meta,
                "chunk_type": "body",
                "chunk_position": position,
                "chunk_index": chunk_idx,
                "total_chunks": total,
            })

    return rows


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------


def _get_existing_arxiv_ids() -> set:
    """Return the set of arxiv_ids already stored in the paper_chunks table."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("arxiv_id")
            .execute()
        )
        return {row["arxiv_id"] for row in (resp.data or []) if row.get("arxiv_id")}
    except Exception as exc:
        logger.warning("Failed to fetch existing arxiv_ids: %s", exc)
        return set()


def _count_chunks() -> int:
    """Return the total number of rows in paper_chunks."""
    try:
        client = get_supabase_client()
        resp = (
            client.table(TABLE_NAME)
            .select("id", count="exact")
            .limit(1)
            .execute()
        )
        return resp.count or 0
    except Exception:
        return 0


def _similarity_search(
    query: str,
    top_k: int = TOP_K_CHUNKS,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[dict]:
    """Run the match_chunks RPC against Supabase pgvector."""
    embedding = _embed_texts([query])[0]
    client = get_supabase_client()
    resp = client.rpc(
        "match_chunks",
        {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": top_k,
        },
    ).execute()
    return resp.data or []


# ---------------------------------------------------------------------------
# Public API (same interface as the old ChromaDB-backed module)
# ---------------------------------------------------------------------------


def search_existing_knowledge(
    topic: str,
    top_k: int = TOP_K_CHUNKS,
    threshold: float = SIMILARITY_THRESHOLD,
    min_chunks: int = MIN_RELEVANT_CHUNKS,
) -> Optional[str]:
    """Query the vector store for existing knowledge on *topic*.

    Returns a formatted context string when enough relevant chunks exist,
    or ``None`` if new papers should be downloaded.
    """
    total = _count_chunks()
    if total == 0:
        logger.info("Knowledge base is empty — must download papers")
        return None

    results = _similarity_search(topic, top_k=top_k, threshold=threshold)

    if len(results) < min_chunks:
        logger.info(
            "Only %d relevant chunks found (need %d) — will download more papers",
            len(results),
            min_chunks,
        )
        return None

    logger.info(
        "Found %d relevant existing chunks for topic — skipping ArXiv download",
        len(results),
    )

    abstract_parts: List[str] = []
    body_parts: List[str] = []
    seen_titles: set = set()

    for row in results:
        title = row.get("title", "Unknown")
        if row.get("chunk_type") == "abstract" and title not in seen_titles:
            seen_titles.add(title)
            abstract_parts.append(f"Title: {title}\nAbstract: {row['content']}")
        else:
            body_parts.append(
                f'[From: "{title}" ({row.get("chunk_position", "")})]\n{row["content"]}'
            )

    context = "--- Paper Abstracts (from knowledge base) ---\n"
    context += (
        "\n\n".join(abstract_parts)
        if abstract_parts
        else "(no abstracts in retrieved chunks)"
    )
    context += "\n\n--- Relevant Excerpts (from knowledge base) ---\n\n"
    context += "\n\n".join(body_parts)
    return context


def add_papers_to_store(papers: List[dict], query: str = "") -> int:
    """Chunk, embed, and insert papers into Supabase pgvector.

    Deduplicates by ``arxiv_id``.  Returns the number of new chunks added.
    """
    existing_ids = _get_existing_arxiv_ids()

    new_papers = [
        p
        for p in papers
        if p.get("arxiv_id", "") and p["arxiv_id"] not in existing_ids
    ]

    if not new_papers:
        logger.info(
            "All %d papers already in knowledge base — skipping embed", len(papers)
        )
        return 0

    logger.info(
        "Adding %d new papers to knowledge base (%d already stored)",
        len(new_papers),
        len(papers) - len(new_papers),
    )

    rows = chunk_papers(new_papers, query=query)
    if not rows:
        return 0

    contents = [r["content"] for r in rows]
    embeddings = _embed_texts(contents)
    for row, emb in zip(rows, embeddings):
        row["embedding"] = emb

    client = get_supabase_client()
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        client.table(TABLE_NAME).upsert(batch).execute()

    logger.info("Stored %d chunks from %d new papers", len(rows), len(new_papers))
    return len(rows)


def retrieve_relevant_chunks(
    papers: List[dict],
    query: str,
    top_k: int = TOP_K_CHUNKS,
) -> str:
    """Ensure papers are stored, then retrieve the most relevant passages."""
    add_papers_to_store(papers, query=query)

    results = _similarity_search(query, top_k=top_k)

    if not results:
        logger.warning("No chunks returned — falling back to abstracts")
        return _format_abstracts_only(papers)

    excerpt_parts: List[str] = []
    for row in results:
        title = row.get("title", "Unknown")
        position = row.get("chunk_position", "")
        chunk_type = row.get("chunk_type", "body")
        tag = f"[{chunk_type}]" if chunk_type == "abstract" else f"[{position}]"
        excerpt_parts.append(f'[From: "{title}" {tag}]\n{row["content"]}')

    abstracts = _format_abstracts_only(papers)
    return (
        abstracts
        + "\n--- Relevant Excerpts (retrieved via semantic search) ---\n\n"
        + "\n\n".join(excerpt_parts)
    )


def vector_search_papers(
    topic: str,
    top_k: int = TOP_K_CHUNKS,
    threshold: float = SIMILARITY_THRESHOLD,
) -> Tuple[str, List[dict]]:
    """Vector similarity search returning both formatted context and ranked paper metadata.

    Used by the hybrid retrieval module to determine which papers should
    undergo PageIndex tree indexing.

    Returns ``(context_string, ranked_papers)`` where *ranked_papers* is a
    list of dicts with ``arxiv_id``, ``title``, ``similarity`` (average
    across chunks), sorted by descending similarity.
    """
    results = _similarity_search(topic, top_k=top_k, threshold=threshold)

    if not results:
        return "", []

    excerpt_parts: List[str] = []
    paper_scores: Dict[str, dict] = {}

    for row in results:
        title = row.get("title", "Unknown")
        arxiv_id = row.get("arxiv_id", "")
        similarity = row.get("similarity", 0.0)
        position = row.get("chunk_position", "")
        chunk_type = row.get("chunk_type", "body")
        tag = f"[{chunk_type}]" if chunk_type == "abstract" else f"[{position}]"
        excerpt_parts.append(f'[From: "{title}" {tag}]\n{row["content"]}')

        if arxiv_id:
            entry = paper_scores.setdefault(
                arxiv_id, {"arxiv_id": arxiv_id, "title": title, "scores": []}
            )
            entry["scores"].append(similarity)

    ranked_papers = sorted(
        [
            {
                "arxiv_id": v["arxiv_id"],
                "title": v["title"],
                "similarity": sum(v["scores"]) / len(v["scores"]),
            }
            for v in paper_scores.values()
        ],
        key=lambda x: x["similarity"],
        reverse=True,
    )

    context = "\n--- Relevant Excerpts (retrieved via semantic search) ---\n\n" + "\n\n".join(
        excerpt_parts
    )
    return context, ranked_papers


def _format_abstracts_only(papers: List[dict]) -> str:
    """Build a context string containing only paper titles and abstracts."""
    parts: List[str] = []
    for p in papers:
        title = p.get("title", "Unknown")
        summary = p.get("summary", "")
        parts.append(f"Title: {title}\nAbstract: {summary}")
    return "--- Paper Abstracts ---\n" + "\n\n".join(parts) + "\n"
