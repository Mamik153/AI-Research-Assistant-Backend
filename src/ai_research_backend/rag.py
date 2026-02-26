import logging
import uuid
from typing import List

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
TOP_K_CHUNKS = 25


def chunk_papers(
    papers: List[dict],
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> tuple[List[str], List[str], List[dict]]:
    """Split full paper content into overlapping text chunks.

    Returns parallel lists of (ids, documents, metadatas) ready for ChromaDB ingestion.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[dict] = []

    for paper_idx, paper in enumerate(papers):
        content = paper.get("content") or ""
        if not content.strip():
            continue

        text_chunks = splitter.split_text(content)
        for chunk_idx, chunk_text in enumerate(text_chunks):
            ids.append(f"p{paper_idx}_c{chunk_idx}")
            documents.append(chunk_text)
            metadatas.append(
                {
                    "title": paper.get("title", "Unknown"),
                    "authors": ", ".join(paper.get("authors", [])),
                    "published": paper.get("published", ""),
                    "paper_index": paper_idx,
                    "chunk_index": chunk_idx,
                }
            )

    return ids, documents, metadatas


def retrieve_relevant_chunks(
    papers: List[dict],
    query: str,
    top_k: int = TOP_K_CHUNKS,
) -> str:
    """Chunk papers, embed in ephemeral ChromaDB, and retrieve the most relevant
    passages. Returns a formatted context string combining paper abstracts with
    semantically retrieved excerpts.

    Falls back to abstracts-only if no chunks can be produced.
    """
    ids, documents, metadatas = chunk_papers(papers)

    if not documents:
        logger.warning("No chunks produced from papers — falling back to abstracts")
        return _format_abstracts_only(papers)

    logger.info("Created %d chunks from %d papers", len(documents), len(papers))

    # Use a unique collection name per call so concurrent/sequential research jobs
    # don't conflict (ChromaDB raises if a collection name already exists).
    client = chromadb.EphemeralClient()
    collection_name = f"research_chunks_{uuid.uuid4().hex}"
    collection = client.create_collection(name=collection_name)
    collection.add(ids=ids, documents=documents, metadatas=metadatas)

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, len(documents)),
    )

    excerpt_parts: List[str] = []
    if results and results["documents"]:
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            title = meta.get("title", "Unknown")
            excerpt_parts.append(f'[From: "{title}"]\n{doc}')

    abstracts = _format_abstracts_only(papers)

    context = (
        abstracts
        + "\n--- Relevant Excerpts (retrieved via semantic search) ---\n\n"
        + "\n\n".join(excerpt_parts)
    )
    return context


def _format_abstracts_only(papers: List[dict]) -> str:
    """Build a context string containing only paper titles and abstracts."""
    parts: List[str] = []
    for p in papers:
        title = p.get("title", "Unknown")
        summary = p.get("summary", "")
        parts.append(f"Title: {title}\nAbstract: {summary}")
    return "--- Paper Abstracts ---\n" + "\n\n".join(parts) + "\n"
