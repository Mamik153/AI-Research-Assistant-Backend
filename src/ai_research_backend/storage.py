"""Supabase Storage wrapper for uploading, downloading, and managing files."""

import json
import logging
import os
from typing import Optional

from supabase import Client, create_client

logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "research-assets")


def get_supabase_client() -> Client:
    """Return a lazily-initialised Supabase client singleton."""
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_KEY must be set in the environment"
            )
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase_client


def upload_file(
    path: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to the storage bucket and return the public URL.

    Overwrites if the file already exists at *path*.
    """
    client = get_supabase_client()
    storage = client.storage.from_(STORAGE_BUCKET)

    try:
        storage.upload(
            path,
            file_bytes,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as exc:
        logger.error("Failed to upload %s: %s", path, exc)
        raise

    return get_public_url(path)


def download_file(path: str) -> bytes:
    """Download file bytes from the storage bucket."""
    client = get_supabase_client()
    storage = client.storage.from_(STORAGE_BUCKET)
    return storage.download(path)


def file_exists(path: str) -> bool:
    """Check whether a file exists in the storage bucket."""
    try:
        download_file(path)
        return True
    except Exception:
        return False


def get_public_url(path: str) -> str:
    """Return the public URL for a file in the storage bucket."""
    client = get_supabase_client()
    storage = client.storage.from_(STORAGE_BUCKET)
    return storage.get_public_url(path)


def delete_files(paths: list[str]) -> None:
    """Delete one or more files from the storage bucket."""
    if not paths:
        return
    client = get_supabase_client()
    storage = client.storage.from_(STORAGE_BUCKET)
    try:
        storage.remove(paths)
    except Exception as exc:
        logger.warning("Failed to delete files: %s", exc)


def upload_json(path: str, data: dict) -> str:
    """Serialise *data* as JSON and upload it. Returns the public URL."""
    raw = json.dumps(data, ensure_ascii=False).encode()
    return upload_file(path, raw, content_type="application/json")


def download_json(path: str) -> Optional[dict]:
    """Download a JSON file and deserialise it, or return None on failure."""
    try:
        raw = download_file(path)
        return json.loads(raw)
    except Exception:
        return None
