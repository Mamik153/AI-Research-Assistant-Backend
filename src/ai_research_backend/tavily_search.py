"""Tavily web search integration for finding research-relevant images.

Searches for architecture diagrams, graphs, and academic figures related
to the research topic. Results supplement images extracted from PDFs.
"""

import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

TAVILY_API_KEY = (os.getenv("TAVILY_API_KEY") or "").strip()

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")

_SEARCH_SUFFIXES = [
    "architecture diagram",
    "research figure graph",
]


def _is_image_url(url: str) -> bool:
    lower = url.lower().split("?")[0]
    return any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS)


def search_research_images(
    topic: str,
    max_results: int = 5,
) -> List[Dict[str, str]]:
    """Search Tavily for research-relevant images on *topic*.

    Returns a list of dicts: {"url": ..., "description": ..., "source": ...}.
    Returns an empty list if Tavily is not configured or the search fails.
    """
    if not TAVILY_API_KEY:
        logger.debug("TAVILY_API_KEY not set — skipping image search")
        return []

    try:
        from tavily import TavilyClient  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("tavily-python not installed — skipping image search")
        return []

    client = TavilyClient(api_key=TAVILY_API_KEY)
    collected: List[Dict[str, str]] = []
    seen_urls: set[str] = set()

    for suffix in _SEARCH_SUFFIXES:
        if len(collected) >= max_results:
            break
        query = f"{topic} {suffix}"
        try:
            response = client.search(
                query=query,
                max_results=5,
                include_images=True,
                search_depth="basic",
            )
        except Exception as exc:
            logger.warning("Tavily search failed for '%s': %s", query, exc)
            continue

        images = response.get("images") or []
        for img in images:
            if len(collected) >= max_results:
                break

            if isinstance(img, str):
                url = img
                description = query
            elif isinstance(img, dict):
                url = img.get("url", "")
                description = img.get("description") or query
            else:
                continue

            if not url or url in seen_urls:
                continue
            if not _is_image_url(url) and "image" not in url.lower():
                continue

            seen_urls.add(url)
            collected.append({
                "url": url,
                "description": description,
                "source": "tavily",
            })

    logger.info("Tavily image search found %d images for '%s'", len(collected), topic)
    return collected
