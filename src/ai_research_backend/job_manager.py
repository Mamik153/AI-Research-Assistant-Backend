import json
import os
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
import uuid

# Directory for storing job results
# Use absolute path relative to the project root (ai_research_backend directory)
RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# In-memory job status tracking
job_statuses: Dict[str, str] = {}
job_topics: Dict[str, str] = {}


def create_job(topic: str) -> str:
    """Create a new job and return its ID"""
    job_id = str(uuid.uuid4())
    job_statuses[job_id] = "pending"
    job_topics[job_id] = topic
    return job_id


def update_job_status(job_id: str, status: str):
    """Update job status"""
    if job_id in job_statuses:
        job_statuses[job_id] = status


def get_job_status(job_id: str) -> Optional[str]:
    """Get job status"""
    return job_statuses.get(job_id)


def save_result(job_id: str, result: dict):
    """Save job result to file system"""
    result_file = RESULTS_DIR / f"{job_id}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def load_result(job_id: str) -> Optional[dict]:
    """Load job result from file system"""
    result_file = RESULTS_DIR / f"{job_id}.json"
    if result_file.exists():
        with open(result_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def job_exists(job_id: str) -> bool:
    """Check if job exists"""
    return job_id in job_statuses


def get_job_topic(job_id: str) -> Optional[str]:
    """Get job topic"""
    return job_topics.get(job_id)

