import logging
import os
import re
import unicodedata
import uuid
from typing import Dict, List, Optional, Tuple

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 300
TOP_K_CHUNKS = 25
CHROMA_DB_PATH = os.path.join(os.getcwd(), "chroma_db")
COLLECTION_NAME = "research_papers"

SIMILARITY_DISTANCE_THRESHOLD = 1.2
MIN_RELEVANT_CHUNKS = 15


def _get_collection() -> chromadb.Collection:
    """Return the persistent research_papers collection (created on first call)."""
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return client.get_or_create_collection(name=COLLECTION_NAME)


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


def _get_existing_arxiv_ids(collection: chromadb.Collection) -> set:
    """Return the set of arxiv_ids already stored in the collection."""
    try:
        all_meta = collection.get(include=["metadatas"])
        ids = set()
        if all_meta and all_meta["metadatas"]:
            for m in all_meta["metadatas"]:
                aid = m.get("arxiv_id", "")
                if aid:
                    ids.add(aid)
        return ids
    except Exception:
        return set()


def chunk_papers(
    papers: List[dict],
    query: str = "",
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
) -> Tuple[List[str], List[str], List[dict]]:
    """Split papers into overlapping text chunks with rich metadata.

    Abstracts are embedded as separate high-signal chunks.
    Returns parallel lists of (ids, documents, metadatas) for ChromaDB ingestion.
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
            chunk_id = f"{arxiv_id}_abstract"
            ids.append(chunk_id)
            documents.append(abstract)
            metadatas.append({
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

            chunk_id = f"{arxiv_id}_body_{chunk_idx}"
            ids.append(chunk_id)
            documents.append(chunk_text)
            metadatas.append({
                **base_meta,
                "chunk_type": "body",
                "chunk_position": position,
                "chunk_index": chunk_idx,
                "total_chunks": total,
            })

    return ids, documents, metadatas


def search_existing_knowledge(
    topic: str,
    top_k: int = TOP_K_CHUNKS,
    distance_threshold: float = SIMILARITY_DISTANCE_THRESHOLD,
    min_chunks: int = MIN_RELEVANT_CHUNKS,
) -> Optional[str]:
    """Query the persistent vector store for existing knowledge on a topic.

    Returns a formatted context string if enough relevant chunks exist,
    or None if new papers should be downloaded.
    """
    collection = _get_collection()
    total_count = collection.count()
    if total_count == 0:
        logger.info("Knowledge base is empty — must download papers")
        return None

    results = collection.query(
        query_texts=[topic],
        n_results=min(top_k, total_count),
        include=["documents", "metadatas", "distances"],
    )

    if not results or not results["documents"] or not results["documents"][0]:
        return None

    relevant_docs = []
    relevant_meta = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if dist <= distance_threshold:
            relevant_docs.append(doc)
            relevant_meta.append(meta)

    if len(relevant_docs) < min_chunks:
        logger.info(
            "Only %d relevant chunks found (need %d) — will download more papers",
            len(relevant_docs),
            min_chunks,
        )
        return None

    logger.info(
        "Found %d relevant existing chunks for topic — skipping ArXiv download",
        len(relevant_docs),
    )

    abstract_parts: List[str] = []
    body_parts: List[str] = []
    seen_titles = set()

    for doc, meta in zip(relevant_docs, relevant_meta):
        title = meta.get("title", "Unknown")
        if meta.get("chunk_type") == "abstract" and title not in seen_titles:
            seen_titles.add(title)
            abstract_parts.append(f"Title: {title}\nAbstract: {doc}")
        else:
            body_parts.append(f'[From: "{title}" ({meta.get("chunk_position", "")})]\n{doc}')

    context = "--- Paper Abstracts (from knowledge base) ---\n"
    context += "\n\n".join(abstract_parts) if abstract_parts else "(no abstracts in retrieved chunks)"
    context += "\n\n--- Relevant Excerpts (from knowledge base) ---\n\n"
    context += "\n\n".join(body_parts)
    return context


def add_papers_to_store(papers: List[dict], query: str = "") -> int:
    """Chunk and embed papers into the persistent vector store.

    Deduplicates by arxiv_id — papers already in the store are skipped.
    Returns the number of new chunks added.
    """
    collection = _get_collection()
    existing_ids = _get_existing_arxiv_ids(collection)

    new_papers = [
        p for p in papers
        if p.get("arxiv_id", "") and p["arxiv_id"] not in existing_ids
    ]

    if not new_papers:
        logger.info("All %d papers already in knowledge base — skipping embed", len(papers))
        return 0

    logger.info(
        "Adding %d new papers to knowledge base (%d already stored)",
        len(new_papers),
        len(papers) - len(new_papers),
    )

    ids, documents, metadatas = chunk_papers(new_papers, query=query)
    if not documents:
        return 0

    batch_size = 500
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i : i + batch_size],
            documents=documents[i : i + batch_size],
            metadatas=metadatas[i : i + batch_size],
        )

    logger.info("Stored %d chunks from %d new papers", len(documents), len(new_papers))
    return len(documents)


def retrieve_relevant_chunks(
    papers: List[dict],
    query: str,
    top_k: int = TOP_K_CHUNKS,
) -> str:
    """Ensure papers are stored, then retrieve the most relevant passages.

    Combines paper abstracts with semantically retrieved excerpts.
    Falls back to abstracts-only if no chunks can be produced.
    """
    add_papers_to_store(papers, query=query)

    collection = _get_collection()
    total_count = collection.count()
    if total_count == 0:
        logger.warning("No chunks in store — falling back to abstracts")
        return _format_abstracts_only(papers)

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, total_count),
        include=["documents", "metadatas", "distances"],
    )

    excerpt_parts: List[str] = []
    if results and results["documents"]:
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            title = meta.get("title", "Unknown")
            position = meta.get("chunk_position", "")
            chunk_type = meta.get("chunk_type", "body")
            tag = f"[{chunk_type}]" if chunk_type == "abstract" else f"[{position}]"
            excerpt_parts.append(f'[From: "{title}" {tag}]\n{doc}')

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
