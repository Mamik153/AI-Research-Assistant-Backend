"""Heuristic image classifier for filtering research-relevant images.

Classifies extracted PDF images as diagram/graph/formula vs. photo/logo/icon
using lightweight pixel-level analysis — no ML models required.
"""

import io
import logging
from typing import Optional, Tuple

from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

# Thresholds -------------------------------------------------------------------
MIN_DIMENSION = 200       # px — reject tiny icons/logos
MAX_DIMENSION = 4000      # px — reject decorative full-page backgrounds
MIN_BYTES = 5_000         # 5 KB — most logos/icons are smaller
MAX_ASPECT_RATIO = 5.0    # width/height or height/width
WHITE_THRESHOLD = 240     # pixel value considered "near white"
MIN_WHITE_RATIO = 0.30    # diagrams/formulas typically have ≥30% white background
MAX_UNIQUE_COLOR_RATIO = 0.15  # ratio of unique colors to total pixels (low = diagram)
EDGE_DENSITY_THRESHOLD = 0.04  # photos have lower structured edge density


def passes_size_filter(image_bytes: bytes) -> bool:
    """Quick byte-length check before any decoding."""
    return len(image_bytes) >= MIN_BYTES


def _open_image(image_bytes: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.load()
        return img
    except Exception:
        return None


def passes_dimension_filter(img: Image.Image) -> bool:
    """Reject images that are too small, too large, or have extreme aspect ratios."""
    w, h = img.size
    if w < MIN_DIMENSION or h < MIN_DIMENSION:
        return False
    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        return False
    ratio = max(w / h, h / w)
    return ratio <= MAX_ASPECT_RATIO


def _white_pixel_ratio(arr: np.ndarray) -> float:
    """Fraction of pixels that are near-white."""
    if arr.ndim == 3:
        gray = np.mean(arr[:, :, :3], axis=2)
    else:
        gray = arr.astype(float)
    return float(np.mean(gray >= WHITE_THRESHOLD))


def _unique_color_ratio(arr: np.ndarray) -> float:
    """Ratio of unique colors to total pixel count (sampled for speed)."""
    if arr.ndim == 3 and arr.shape[2] >= 3:
        flat = arr[:, :, :3].reshape(-1, 3)
    else:
        return 1.0
    total = flat.shape[0]
    if total > 50_000:
        indices = np.random.default_rng(42).choice(total, 50_000, replace=False)
        flat = flat[indices]
        total = 50_000
    quantized = (flat // 16).astype(np.uint8)
    unique = np.unique(quantized, axis=0).shape[0]
    return unique / total


def _edge_density(arr: np.ndarray) -> float:
    """Simple Sobel-like edge density on grayscale image."""
    if arr.ndim == 3:
        gray = np.mean(arr[:, :, :3], axis=2)
    else:
        gray = arr.astype(float)
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    gx = np.abs(gray[:-2, 1:-1] - gray[2:, 1:-1])
    gy = np.abs(gray[1:-1, :-2] - gray[1:-1, 2:])
    combined = (gx[:min(gx.shape[0], gy.shape[0]), :min(gx.shape[1], gy.shape[1])] +
                gy[:min(gx.shape[0], gy.shape[0]), :min(gx.shape[1], gy.shape[1])])
    edge_pixels = np.sum(combined > 30)
    return float(edge_pixels / combined.size) if combined.size > 0 else 0.0


def classify_image(image_bytes: bytes) -> str:
    """Classify an image as research-relevant or not.

    Returns one of: 'diagram', 'photo', 'logo', 'unknown'.
    Only 'diagram' images should be kept for the research output.
    """
    img = _open_image(image_bytes)
    if img is None:
        return "unknown"

    if not passes_dimension_filter(img):
        return "logo"

    img_rgb = img.convert("RGB")
    arr = np.array(img_rgb)

    white_ratio = _white_pixel_ratio(arr)
    color_ratio = _unique_color_ratio(arr)
    edge_dens = _edge_density(arr)

    # Diagrams/formulas: high white background, low color diversity, structured edges
    if white_ratio >= MIN_WHITE_RATIO and color_ratio <= MAX_UNIQUE_COLOR_RATIO:
        return "diagram"

    # High white ratio with moderate edge density = likely a graph or formula
    if white_ratio >= 0.40 and edge_dens >= EDGE_DENSITY_THRESHOLD:
        return "diagram"

    # Very colorful with no white background = photo
    if white_ratio < 0.20 and color_ratio > 0.10:
        return "photo"

    # Moderate signals — lean toward keeping if it has structured edges
    if edge_dens >= EDGE_DENSITY_THRESHOLD and white_ratio >= 0.25:
        return "diagram"

    return "photo"


def is_research_relevant(image_bytes: bytes) -> bool:
    """Top-level filter: returns True only for research-relevant images."""
    if not passes_size_filter(image_bytes):
        return False
    classification = classify_image(image_bytes)
    return classification == "diagram"


def is_header_region(
    page_index: int,
    bbox: Tuple[float, float, float, float],
    page_height: float,
) -> bool:
    """Check if image bounding box is in the header region of page 0."""
    if page_index != 0:
        return False
    _, y0, _, _ = bbox
    return y0 < page_height * 0.15
