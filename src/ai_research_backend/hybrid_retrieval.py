"""Two-stage hybrid retrieval: vector search (Supabase pgvector) + PageIndex tree reasoning.

Stage 1 — broad vector similarity search via ``rag.vector_search_papers``.
Stage 2 — deep tree-based reasoning via ``tree_index`` for the top-N papers.
Results are fused with source attribution.
"""

import logging
from typing import List, Optional

from ai_research_backend.rag import (
    add_papers_to_store,
    retrieve_relevant_chunks,
    vector_search_papers,
    _format_abstracts_only,
)
from ai_research_backend.tree_index import (
    get_tree_context,
    is_enabled as pageindex_enabled,
    PAGEINDEX_MAX_TREE_PAPERS,
)

logger = logging.getLogger(__name__)


def hybrid_retrieve(
    papers: List[dict],
    topic: str,
    top_k_tree_papers: int = PAGEINDEX_MAX_TREE_PAPERS,
) -> str:
    """Main entry point for hybrid retrieval.

    When PageIndex is disabled, falls back to vector-only retrieval
    (equivalent to ``retrieve_relevant_chunks``).
    """
    add_papers_to_store(papers, query=topic)

    if not pageindex_enabled():
        logger.info("PageIndex disabled — using vector-only retrieval")
        return retrieve_relevant_chunks(papers, topic)

    vector_context, ranked_papers = _vector_retrieve(papers, topic)
    tree_context = _tree_retrieve(ranked_papers, papers, topic, top_k_tree_papers)

    return _fuse_contexts(
        vector_context=vector_context,
        tree_context=tree_context,
        papers=papers,
    )


def _vector_retrieve(
    papers: List[dict],
    topic: str,
) -> tuple[str, list[dict]]:
    """Stage 1: Supabase pgvector search. Returns context + ranked paper list."""
    context, ranked = vector_search_papers(topic)
    if not context:
        context = _format_abstracts_only(papers)
    return context, ranked


def _tree_retrieve(
    ranked_papers: list[dict],
    all_papers: List[dict],
    topic: str,
    top_k: int,
) -> Optional[str]:
    """Stage 2: PageIndex tree reasoning for top-N papers."""
    papers_by_id = {p.get("arxiv_id", ""): p for p in all_papers if p.get("arxiv_id")}

    candidates = ranked_papers[:top_k]
    if not candidates:
        logger.info("No ranked papers for tree retrieval")
        return None

    tree_parts: List[str] = []
    for paper_meta in candidates:
        arxiv_id = paper_meta.get("arxiv_id", "")
        title = paper_meta.get("title", "Unknown")
        pdf_storage_path = papers_by_id.get(arxiv_id, {}).get("pdf_storage_path", "")

        if not pdf_storage_path:
            pdf_storage_path = f"pdfs/{arxiv_id}.pdf"

        logger.info("Running tree retrieval for %s (%s)", arxiv_id, title)
        ctx = get_tree_context(arxiv_id, pdf_storage_path, topic)
        if ctx:
            tree_parts.append(f'=== Tree-retrieved from: "{title}" ===\n{ctx}')

    if not tree_parts:
        return None

    return "\n\n".join(tree_parts)


def _fuse_contexts(
    vector_context: str,
    tree_context: Optional[str],
    papers: List[dict],
) -> str:
    """Merge vector and tree contexts with source attribution."""
    parts = [_format_abstracts_only(papers)]

    if vector_context:
        parts.append("[vector-search]\n" + vector_context)

    if tree_context:
        parts.append("[tree-search]\n" + tree_context)

    return "\n\n".join(parts)
