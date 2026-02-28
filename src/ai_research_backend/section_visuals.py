"""Generate section-level visual assets: rendered math equations and data charts.

All images are rendered in-memory and uploaded to Supabase Storage.
"""

import io
import logging
import re
from typing import Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ai_research_backend.storage import upload_file

logger = logging.getLogger(__name__)

MATH_PATTERN = re.compile(
    r"""
    \$\$(.+?)\$\$       |   # display math $$...$$
    \\\[(.+?)\\\]        |   # display math \[...\]
    \$([^$\n]+?)\$           # inline math $...$
    """,
    re.DOTALL | re.VERBOSE,
)


def _render_latex_to_bytes(latex: str, dpi: int = 150) -> Optional[bytes]:
    """Render a LaTeX math string to PNG bytes. Returns None on failure."""
    try:
        fig, ax = plt.subplots(figsize=(6, 1.2))
        ax.axis("off")
        ax.text(
            0.5, 0.5, f"${latex.strip()}$",
            fontsize=18, ha="center", va="center",
            transform=ax.transAxes,
        )
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.1, transparent=True)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.debug("Failed to render LaTeX '%s': %s", latex[:60], e)
        plt.close("all")
        return None


def render_section_math(
    structured_sections: dict,
    job_id: str,
) -> Dict[str, List[str]]:
    """Scan section text for LaTeX math, render each to PNG and upload.

    Returns a dict mapping section keys to lists of public image URLs.
    """
    result: Dict[str, List[str]] = {}

    text_fields = {
        "overview": lambda s: [s.get("content", "")] if isinstance(s, dict) else [],
        "key_concepts": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "benefits": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "risks": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "applications": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "future_directions": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "methodologies": lambda items: [
            it.get("description", "") for it in items
        ] if isinstance(items, list) else [],
        "statistics": lambda items: [
            it.get("context", "") for it in items
        ] if isinstance(items, list) else [],
    }

    for section_key, extractor in text_fields.items():
        section_data = structured_sections.get(section_key)
        if not section_data:
            continue
        texts = extractor(section_data)
        combined = " ".join(t for t in texts if t)
        if not combined:
            continue

        matches = MATH_PATTERN.findall(combined)
        if not matches:
            continue

        urls: List[str] = []
        for idx, groups in enumerate(matches):
            latex = next((g for g in groups if g), None)
            if not latex or len(latex.strip()) < 2:
                continue
            png_bytes = _render_latex_to_bytes(latex)
            if png_bytes is None:
                continue
            storage_path = f"generated_math/{job_id}_{section_key}_{idx}.png"
            try:
                url = upload_file(storage_path, png_bytes, "image/png")
                urls.append(url)
            except Exception as exc:
                logger.warning("Math image upload failed: %s", exc)
        if urls:
            result[section_key] = urls

    return result


def generate_statistics_chart(
    structured_sections: dict,
    job_id: str,
) -> Optional[str]:
    """Generate a bar chart from the statistics section data.

    Returns a public image URL or None if no chart could be generated.
    """
    stats = structured_sections.get("statistics")
    if not stats or not isinstance(stats, list) or len(stats) < 2:
        return None

    labels: List[str] = []
    values: List[float] = []
    for item in stats:
        if not isinstance(item, dict):
            continue
        label = item.get("label", "")
        raw_val = item.get("value", "")
        try:
            numeric = float(str(raw_val).replace(",", "").replace("+", "").strip())
            labels.append(label[:30])
            values.append(numeric)
        except (ValueError, TypeError):
            continue

    if len(values) < 2:
        return None

    try:
        fig, ax = plt.subplots(figsize=(max(6, len(values) * 1.2), 4))
        ax.bar(range(len(values)), values, color="#4A90D9", edgecolor="white")
        ax.set_xticks(range(len(values)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("Value")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        storage_path = f"generated_charts/{job_id}_statistics.png"
        return upload_file(storage_path, buf.read(), "image/png")
    except Exception as e:
        logger.debug("Failed to generate statistics chart: %s", e)
        plt.close("all")
        return None


def generate_comparison_chart(
    structured_sections: dict,
    job_id: str,
) -> Optional[str]:
    """Generate a grouped bar chart from the comparisons section.

    Returns a public image URL or None if not possible.
    """
    comp = structured_sections.get("comparisons")
    if not comp or not isinstance(comp, dict):
        return None
    criteria = comp.get("criteria", [])
    items = comp.get("items", [])
    if not criteria or not items or len(items) < 2:
        return None

    has_numeric = False
    numeric_items: List[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        vals = item.get("values", [])
        parsed = []
        for v in vals:
            try:
                parsed.append(float(str(v).replace(",", "").replace("+", "").strip()))
                has_numeric = True
            except (ValueError, TypeError):
                parsed.append(0.0)
        numeric_items.append({"name": item.get("name", ""), "values": parsed})

    if not has_numeric or len(numeric_items) < 2:
        return None

    try:
        import numpy as np

        n_criteria = len(criteria)
        n_items = len(numeric_items)
        x = np.arange(n_criteria)
        width = 0.8 / n_items
        colors = plt.cm.Set2(np.linspace(0, 1, n_items))

        fig, ax = plt.subplots(figsize=(max(6, n_criteria * 1.5), 4))
        for i, item in enumerate(numeric_items):
            vals = item["values"][:n_criteria]
            while len(vals) < n_criteria:
                vals.append(0.0)
            ax.bar(x + i * width, vals, width, label=item["name"][:20], color=colors[i])

        ax.set_xticks(x + width * (n_items - 1) / 2)
        ax.set_xticklabels([c[:20] for c in criteria], rotation=30, ha="right", fontsize=9)
        ax.legend(fontsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)

        storage_path = f"generated_charts/{job_id}_comparisons.png"
        return upload_file(storage_path, buf.read(), "image/png")
    except Exception as e:
        logger.debug("Failed to generate comparison chart: %s", e)
        plt.close("all")
        return None
