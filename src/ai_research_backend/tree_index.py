"""PageIndex tree-based reasoning retrieval.

Generates hierarchical tree structures from PDFs, caches them in Supabase
Storage, and performs LLM-driven tree search to extract precisely relevant
document sections.

Supports two modes controlled by ``PAGEINDEX_MODE``:
  * ``self_hosted`` — uses the open-source ``pageindex`` package + Ollama
  * ``cloud`` — uses the PageIndex Cloud API (requires ``PAGEINDEX_API_KEY``)
"""

import json
import logging
import os
import tempfile
from typing import List, Optional

import fitz  # pymupdf

from ai_research_backend.storage import (
    download_file,
    download_json,
    upload_json,
)

logger = logging.getLogger(__name__)

PAGEINDEX_ENABLED = os.getenv("PAGEINDEX_ENABLED", "false").lower() == "true"
PAGEINDEX_MODE = os.getenv("PAGEINDEX_MODE", "self_hosted")
PAGEINDEX_API_KEY = os.getenv("PAGEINDEX_API_KEY", "")
PAGEINDEX_MODEL = (
    os.getenv("PAGEINDEX_MODEL")
    or os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
)
PAGEINDEX_MAX_TREE_PAPERS = int(os.getenv("PAGEINDEX_MAX_TREE_PAPERS", "3"))


def is_enabled() -> bool:
    return PAGEINDEX_ENABLED


# ---------------------------------------------------------------------------
# Tree cache (Supabase Storage)
# ---------------------------------------------------------------------------

_TREE_PREFIX = "tree_indexes"


def load_cached_tree(arxiv_id: str) -> Optional[dict]:
    """Load a cached tree index from Supabase Storage, or ``None``."""
    path = f"{_TREE_PREFIX}/{arxiv_id}.json"
    return download_json(path)


def save_tree_cache(arxiv_id: str, tree: dict) -> None:
    """Upload tree JSON to Supabase Storage."""
    path = f"{_TREE_PREFIX}/{arxiv_id}.json"
    upload_json(path, tree)
    logger.info("Cached tree index for %s", arxiv_id)


# ---------------------------------------------------------------------------
# Tree generation
# ---------------------------------------------------------------------------


def generate_tree(pdf_bytes: bytes, arxiv_id: str) -> Optional[dict]:
    """Build a PageIndex tree from raw PDF bytes.

    Returns the tree dict on success, or ``None`` on failure.
    """
    if PAGEINDEX_MODE == "cloud":
        return _generate_tree_cloud(pdf_bytes, arxiv_id)
    return _generate_tree_self_hosted(pdf_bytes, arxiv_id)


def _generate_tree_self_hosted(pdf_bytes: bytes, arxiv_id: str) -> Optional[dict]:
    """Generate a tree via the open-source ``pageindex`` package."""
    try:
        from pageindex import PageIndex
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("OLLAMA_API_KEY", ""),
            base_url=os.getenv("OLLAMA_API_BASE", "https://ollama.com/v1"),
        )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            index = PageIndex(tmp_path, client=client, model=PAGEINDEX_MODEL)
            tree_data = index.to_dict() if hasattr(index, "to_dict") else {"raw": str(index)}
            logger.info("Generated tree index for %s (self_hosted)", arxiv_id)
            return tree_data
        finally:
            os.unlink(tmp_path)

    except ImportError:
        logger.error("pageindex package not installed — cannot generate tree")
        return None
    except Exception as exc:
        logger.error("Tree generation failed for %s: %s", arxiv_id, exc)
        return None


def _generate_tree_cloud(pdf_bytes: bytes, arxiv_id: str) -> Optional[dict]:
    """Generate a tree via the PageIndex Cloud API."""
    if not PAGEINDEX_API_KEY:
        logger.error("PAGEINDEX_API_KEY not set — cannot use cloud mode")
        return None
    try:
        from pageindex.cloud import PageIndexCloud

        cloud = PageIndexCloud(api_key=PAGEINDEX_API_KEY)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        try:
            tree_data = cloud.index(tmp_path)
            logger.info("Generated tree index for %s (cloud)", arxiv_id)
            return tree_data if isinstance(tree_data, dict) else {"raw": str(tree_data)}
        finally:
            os.unlink(tmp_path)

    except ImportError:
        logger.error("pageindex cloud module not available")
        return None
    except Exception as exc:
        logger.error("Cloud tree generation failed for %s: %s", arxiv_id, exc)
        return None


# ---------------------------------------------------------------------------
# Tree search (LLM-based reasoning)
# ---------------------------------------------------------------------------


def tree_search(tree: dict, query: str) -> List[dict]:
    """Use LLM reasoning to navigate the tree and identify relevant sections.

    Returns a list of dicts with ``page_start``, ``page_end``, and
    ``reason`` for each matched section.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("OLLAMA_API_KEY", ""),
        base_url=os.getenv("OLLAMA_API_BASE", "https://ollama.com/v1"),
    )

    tree_str = json.dumps(tree, indent=2, ensure_ascii=False)
    if len(tree_str) > 12000:
        tree_str = tree_str[:12000] + "\n... (truncated)"

    prompt = f"""You are a research librarian. Given this hierarchical document tree structure and a research query, identify the most relevant sections.

Document tree:
{tree_str}

Research query: {query}

Return a JSON array of objects, each with:
- "page_start": starting page number (0-indexed)
- "page_end": ending page number (0-indexed, inclusive)
- "reason": brief explanation of why this section is relevant

Return ONLY the JSON array, no other text. Select at most 5 sections."""

    try:
        response = client.chat.completions.create(
            model=PAGEINDEX_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        sections = json.loads(content)
        if not isinstance(sections, list):
            return []
        return sections
    except Exception as exc:
        logger.error("Tree search failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Section text extraction
# ---------------------------------------------------------------------------


def extract_sections_from_pdf(
    pdf_bytes: bytes,
    sections: List[dict],
) -> List[str]:
    """Extract text from the specified page ranges of a PDF."""
    if not sections:
        return []

    texts: List[str] = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        for sec in sections:
            start = max(0, int(sec.get("page_start", 0)))
            end = min(total_pages - 1, int(sec.get("page_end", start)))
            section_text = ""
            for page_num in range(start, end + 1):
                section_text += doc[page_num].get_text()
            if section_text.strip():
                reason = sec.get("reason", "")
                header = f"[Tree-search: pages {start}-{end}] {reason}\n" if reason else ""
                texts.append(header + section_text.strip())
        doc.close()
    except Exception as exc:
        logger.error("Failed to extract sections from PDF: %s", exc)

    return texts


# ---------------------------------------------------------------------------
# High-level: get or create tree, search, extract
# ---------------------------------------------------------------------------


def get_tree_context(
    arxiv_id: str,
    pdf_storage_path: str,
    query: str,
) -> Optional[str]:
    """End-to-end: load/generate tree, search it, extract relevant text.

    Returns a formatted context string, or ``None`` on failure.
    """
    tree = load_cached_tree(arxiv_id)

    if tree is None:
        try:
            pdf_bytes = download_file(pdf_storage_path)
        except Exception as exc:
            logger.error("Cannot download PDF for %s: %s", arxiv_id, exc)
            return None

        tree = generate_tree(pdf_bytes, arxiv_id)
        if tree is None:
            return None
        save_tree_cache(arxiv_id, tree)
    else:
        logger.info("Using cached tree index for %s", arxiv_id)

    sections = tree_search(tree, query)
    if not sections:
        logger.info("Tree search returned no relevant sections for %s", arxiv_id)
        return None

    try:
        pdf_bytes = download_file(pdf_storage_path)
    except Exception as exc:
        logger.error("Cannot download PDF for extraction %s: %s", arxiv_id, exc)
        return None

    texts = extract_sections_from_pdf(pdf_bytes, sections)
    if not texts:
        return None

    return "\n\n".join(texts)
