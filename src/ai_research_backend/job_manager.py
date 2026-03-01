import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS = 7200  # 2 hours
MAX_STORED_JOBS = 500
_EVENT_QUEUE_MAX = 256

# In-memory job status tracking
job_statuses: Dict[str, str] = {}
job_topics: Dict[str, str] = {}
job_created_at: Dict[str, float] = {}

# Chain of thought tracking
job_progress: Dict[str, dict] = {}
job_thoughts: Dict[str, list] = {}
job_findings: Dict[str, list] = {}

# In-memory job results (optional Supabase persistence when configured)
job_results: Dict[str, dict] = {}

# SSE event queues — one asyncio.Queue per job for streaming events to clients
_event_queues: Dict[str, asyncio.Queue] = {}
_event_queue_lock = threading.Lock()

_eviction_lock = threading.Lock()


def _evict_expired_jobs() -> None:
    """Remove completed/failed jobs older than JOB_TTL_SECONDS."""
    now = time.monotonic()
    terminal = ("completed", "failed")
    to_remove = [
        jid
        for jid, created in job_created_at.items()
        if (now - created) > JOB_TTL_SECONDS and job_statuses.get(jid) in terminal
    ]
    if not to_remove and len(job_statuses) > MAX_STORED_JOBS:
        sorted_jobs = sorted(
            (
                (jid, ts)
                for jid, ts in job_created_at.items()
                if job_statuses.get(jid) in terminal
            ),
            key=lambda x: x[1],
        )
        excess = len(job_statuses) - MAX_STORED_JOBS
        to_remove = [jid for jid, _ in sorted_jobs[:excess]]

    for jid in to_remove:
        job_statuses.pop(jid, None)
        job_topics.pop(jid, None)
        job_created_at.pop(jid, None)
        job_progress.pop(jid, None)
        job_thoughts.pop(jid, None)
        job_findings.pop(jid, None)
        job_results.pop(jid, None)
        with _event_queue_lock:
            _event_queues.pop(jid, None)
        _delete_result_from_storage(jid)
    if to_remove:
        logger.info("Evicted %d expired jobs from memory", len(to_remove))


def create_job(topic: str) -> str:
    """Create a new job and return its ID"""
    with _eviction_lock:
        _evict_expired_jobs()
    job_id = str(uuid.uuid4())
    job_statuses[job_id] = "pending"
    job_topics[job_id] = topic
    job_created_at[job_id] = time.monotonic()
    return job_id


def update_job_status(job_id: str, status: str):
    """Update job status"""
    if job_id in job_statuses:
        job_statuses[job_id] = status


def get_job_status(job_id: str) -> Optional[str]:
    """Get job status"""
    return job_statuses.get(job_id)


def count_ongoing_jobs() -> int:
    """Return the number of jobs that are pending or running."""
    ongoing = ("pending", "running")
    return sum(1 for s in job_statuses.values() if s in ongoing)


def _delete_result_from_storage(job_id: str) -> None:
    """Remove result JSON from Supabase storage if configured. No-op on failure."""
    try:
        from ai_research_backend.storage import delete_files
        delete_files([f"results/{job_id}.json"])
    except Exception as exc:
        logger.debug("Storage delete skipped for %s: %s", job_id, exc)


def save_result(job_id: str, result: dict) -> None:
    """Save job result in memory and optionally to Supabase Storage."""
    job_results[job_id] = result
    try:
        from ai_research_backend.storage import upload_json
        upload_json(f"results/{job_id}.json", result)
    except Exception as exc:
        logger.warning("Result upload to storage skipped for %s: %s", job_id, exc)


def load_result(job_id: str) -> Optional[dict]:
    """Load job result from memory, or from Supabase Storage if not in memory."""
    if job_id in job_results:
        return job_results[job_id]
    try:
        from ai_research_backend.storage import download_json
        data = download_json(f"results/{job_id}.json")
        if isinstance(data, dict):
            job_results[job_id] = data
            return data
    except Exception as exc:
        logger.debug("Result load from storage skipped for %s: %s", job_id, exc)
    return None


def job_exists(job_id: str) -> bool:
    """Check if job exists"""
    return job_id in job_statuses


def get_job_topic(job_id: str) -> Optional[str]:
    """Get job topic"""
    return job_topics.get(job_id)


def update_job_progress(
    job_id: str, current_step: str, progress: int, thought_message: Optional[str] = None
):
    """Update job progress and optionally add a chain of thought message"""
    if job_id not in job_progress:
        job_progress[job_id] = {}
        job_thoughts[job_id] = []

    job_progress[job_id] = {
        "current_step": current_step,
        "progress_percentage": progress,
    }

    if thought_message:
        timestamp = datetime.now().strftime("%H:%M:%S")
        job_thoughts[job_id].append(f"[{timestamp}] {thought_message}")


def get_job_progress(job_id: str) -> Optional[dict]:
    """Get current job progress and chain of thought"""
    if job_id not in job_progress:
        return None

    return {
        "current_step": job_progress[job_id].get("current_step"),
        "progress_percentage": job_progress[job_id].get("progress_percentage"),
        "chain_of_thought": job_thoughts.get(job_id, []),
        "intermediate_findings": job_findings.get(job_id, []),
    }


def add_intermediate_finding(job_id: str, finding: str):
    """Add an intermediate finding to the job"""
    if job_id not in job_findings:
        job_findings[job_id] = []
    job_findings[job_id].append(finding)


# ---------------------------------------------------------------------------
# SSE event queue helpers
# ---------------------------------------------------------------------------

def create_event_queue(job_id: str) -> asyncio.Queue:
    """Create (or return existing) event queue for a job."""
    with _event_queue_lock:
        q = _event_queues.get(job_id)
        if q is None:
            q = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX)
            _event_queues[job_id] = q
        return q


def get_event_queue(job_id: str) -> Optional[asyncio.Queue]:
    """Return the event queue for a job, or None if not created."""
    with _event_queue_lock:
        return _event_queues.get(job_id)


def push_event(job_id: str, event_type: str, data: Any) -> None:
    """Push an SSE event onto the job's queue (non-blocking, thread-safe).

    Called from background threads — uses put_nowait so it never blocks
    the research pipeline. Silently drops if queue is full or missing.
    """
    with _event_queue_lock:
        q = _event_queues.get(job_id)
    if q is None:
        return
    try:
        q.put_nowait({"event": event_type, "data": data})
    except asyncio.QueueFull:
        logger.debug("SSE event queue full for job %s, dropping event", job_id)
