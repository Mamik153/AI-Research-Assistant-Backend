import hmac
import json
import logging
import os
import re
import uuid as uuid_mod
from datetime import datetime
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from ai_research_backend.models import (
    ResearchRequest,
    JobStatusResponse,
    ResearchResultResponse,
    DynamicResearchResultResponse,
    ErrorResponse,
    StructuredSections,
)
from ai_research_backend.job_manager import (
    create_job,
    update_job_status,
    get_job_status,
    save_result,
    load_result,
    job_exists,
    get_job_topic,
    update_job_progress,
    get_job_progress,
    add_intermediate_finding,
    count_ongoing_jobs,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Eagerly import heavy research modules so failures surface at startup (or at
# least on the first import attempt) rather than silently hanging inside a
# background task.  Each block is isolated so one broken dependency does not
# prevent the rest from loading.
# ---------------------------------------------------------------------------
try:
    from ai_research_backend.llm_config import active_llm, sub_llm
except Exception:
    logger.warning("Could not import llm_config module — LLMs will be unavailable", exc_info=True)
    active_llm = None  # type: ignore[assignment]
    sub_llm = None  # type: ignore[assignment]

try:
    from ai_research_backend.rag import search_existing_knowledge
except Exception:
    logger.warning("Could not import rag module — knowledge-base search unavailable", exc_info=True)
    search_existing_knowledge = None  # type: ignore[assignment]

try:
    from ai_research_backend.hybrid_retrieval import hybrid_retrieve
except Exception:
    logger.warning("Could not import hybrid_retrieval module", exc_info=True)
    hybrid_retrieve = None  # type: ignore[assignment]

try:
    from ai_research_backend.agents import run_research_agents
except Exception:
    logger.warning("Could not import agents module", exc_info=True)
    run_research_agents = None  # type: ignore[assignment]

API_KEY = (os.getenv("API_KEY") or "").strip()
RATE_LIMIT_STRING = os.getenv("RATE_LIMIT_PER_MINUTE", "10") + "/minute"
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_RESEARCH_JOBS", "1"))

SECTION_KEYS = frozenset(
    {
        "overview",
        "key_concepts",
        "benefits",
        "risks",
        "applications",
        "future_directions",
        "methodologies",
        "comparisons",
        "timeline",
        "statistics",
    }
)

MAX_REQUEST_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    # Paths that serve Swagger UI / ReDoc and need relaxed CSP for CDN + inline scripts
    _DOCS_PATHS = frozenset({"/docs", "/docs/", "/redoc", "/redoc/", "/openapi.json"})

    # CSP that allows Swagger UI assets: cdn.jsdelivr.net, fastapi favicon, inline script/style
    _CSP_DOCS = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "img-src 'self' https://fastapi.tiangolo.com https://cdn.jsdelivr.net"
    )
    _CSP_DEFAULT = "default-src 'self'"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        csp = (
            self._CSP_DOCS
            if request.url.path in self._DOCS_PATHS
            else self._CSP_DEFAULT
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds a configured limit."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large."},
            )
        return await call_next(request)


app = FastAPI(title="AI Research Backend API", version="1.0.0")

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


def _rate_limit_exceeded_handler_custom(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Too many requests. Try again later.",
            "code": "RATE_LIMIT_EXCEEDED",
        },
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler_custom)


async def verify_api_key(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> None:
    """Require a valid API key via Authorization: Bearer <key> or X-API-Key: <key>."""
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    if not API_KEY or not token or not hmac.compare_digest(token, API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_origin_regex=r"^https://([a-z0-9-]+\.)*slickspender\.com$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
)

def _validate_job_id(job_id: str) -> None:
    """Validate that job_id is a proper UUID to prevent path traversal."""
    try:
        uuid_mod.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")


def _parse_structured_sections(data: Optional[dict]) -> StructuredSections:
    """Parse raw dict from LLM into StructuredSections; return empty on failure."""
    if not data or not isinstance(data, dict):
        return StructuredSections()
    try:
        return StructuredSections(**data)
    except Exception:
        return StructuredSections()


def _normalize_section_confidence(raw: Optional[dict]) -> Optional[Dict[str, float]]:
    """Normalize section_confidence: keep only known keys, clamp values to [0, 1]."""
    if not raw or not isinstance(raw, dict):
        return None
    result = {}
    for key in SECTION_KEYS:
        val = raw.get(key)
        if val is None:
            continue
        try:
            score = float(val)
            result[key] = max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            continue
    return result if result else None


def _normalize_section_images(
    raw: Optional[dict],
    allowed_urls: set[str],
) -> Optional[Dict[str, List[str]]]:
    """Normalize section_images: keep only known section keys and allowed image URLs."""
    if not raw or not isinstance(raw, dict):
        return None
    result: Dict[str, List[str]] = {}
    for key in SECTION_KEYS:
        urls = raw.get(key)
        if not urls or not isinstance(urls, list):
            continue
        filtered = [u for u in urls if isinstance(u, str) and u in allowed_urls]
        if filtered:
            result[key] = filtered
    return result if result else None


def _unwrap_llm_response(llm_data: Optional[dict]) -> dict:
    """
    If the LLM put the full JSON inside the 'summary' field (double-encoded),
    parse that inner string and return the real payload. Otherwise return llm_data.
    """
    if not llm_data or not isinstance(llm_data, dict):
        return llm_data or {}
    summary_val = llm_data.get("summary")
    if not isinstance(summary_val, str):
        return llm_data
    inner = summary_val.strip()
    if not inner.startswith("{"):
        return llm_data
    try:
        parsed = json.loads(inner)
        if not isinstance(parsed, dict):
            return llm_data
        # Prefer unwrapped if it has the expected structure
        if any(
            k in parsed
            for k in ("key_insights", "structured_sections", "generated_diagrams")
        ):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return llm_data


def _extract_inner_json_from_response(raw: str) -> Optional[dict]:
    """
    When the LLM returns outer JSON with an unescaped inner JSON string (invalid outer),
    try to find and parse the inner JSON object (the one with summary/key_insights/structured_sections).
    """
    if not raw or ("structured_sections" not in raw and "key_insights" not in raw):
        return None
    # Find start of inner object: {" then optional whitespace then "summary" or "key_insights"
    start_match = re.search(r'\{\s*"(?:summary|key_insights)"', raw)
    if not start_match:
        return None
    start = start_match.start()
    depth = 0
    in_string = False
    escape = False
    quote_char = None
    i = start
    while i < len(raw):
        c = raw[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\" and in_string:
            escape = True
            i += 1
            continue
        if not in_string:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(raw[start : i + 1])
                        if isinstance(candidate, dict) and any(
                            k in candidate
                            for k in ("summary", "key_insights", "structured_sections")
                        ):
                            return _unwrap_llm_response(candidate)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
            elif c in ('"', "'"):
                in_string = True
                quote_char = c
            i += 1
            continue
        if c == quote_char:
            in_string = False
        i += 1
    return None


def _repair_truncated_json(raw: str) -> Optional[dict]:
    """Attempt to repair truncated JSON by closing unclosed structures."""
    if not raw or not raw.strip().startswith("{"):
        return None
    open_braces = raw.count("{") - raw.count("}")
    open_brackets = raw.count("[") - raw.count("]")
    repaired = raw.strip()
    repaired += "]" * max(0, open_brackets)
    repaired += "}" * max(0, open_braces)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def _validate_llm_response(llm_data: dict) -> tuple[bool, str]:
    """Validate that LLM response has minimum required fields and structure."""
    if not isinstance(llm_data, dict):
        return False, "Not a dict"

    required_keys = ["summary", "key_insights", "structured_sections"]
    missing = [k for k in required_keys if k not in llm_data]
    if missing:
        return False, f"Missing keys: {missing}"

    # Check structured_sections has at least some data
    sections = llm_data.get("structured_sections", {})
    if not isinstance(sections, dict):
        return False, "structured_sections not a dict"

    # At least one section should have data
    has_data = any(
        sections.get(k) for k in ["overview", "key_concepts", "benefits", "risks"]
    )
    if not has_data:
        return False, "structured_sections is empty"

    return True, "OK"


_MERMAID_DIAGRAM_PREFIXES = (
    "graph",
    "flowchart",
    "sequencediagram",
    "classdiagram",
    "statediagram",
    "erdiagram",
    "gantt",
    "pie",
    "journey",
)

_MERMAID_EDGE_OR_NODE_RE = re.compile(
    r"(-->|---|->|<-|==>|===|\|\||\[.*?\]|\(.*?\)|\{.*?\})"
)

_MERMAID_SPECIAL_LABEL_CHARS = re.compile(r'[():{}&<>"]')

# Bare quoted node: "Label" not inside brackets (left of edge); capture arrow
_MERMAID_BARE_QUOTED_LEFT_RE = re.compile(
    r'(?<!\])"([^"]+)"\s*((?:-->|--|---|->|<-|===|==>))'
)
# Bare quoted node: "Label" not inside brackets (right of edge)
_MERMAID_BARE_QUOTED_RIGHT_RE = re.compile(
    r'(-->|--|---|->|<-|===|==>)\s*"([^"]+)"(?!\])'
)


def _label_to_node_id(label: str, max_length: int = 30) -> str:
    """Convert a display label to a Mermaid node ID (no spaces, camelCase)."""
    s = "".join(
        w.capitalize() for w in re.sub(r"[^a-zA-Z0-9\s]", " ", label).split()
    )
    return s[:max_length] if s else "N"


def _repair_flowchart_bare_quoted_nodes(diagram: str) -> str:
    """Replace bare quoted nodes like \"Label\" with Id[\"Label\"] in flowchart/graph only."""
    lines = diagram.split("\n")
    if not lines:
        return diagram
    first = lines[0].strip().split()
    if not first:
        return diagram
    prefix = first[0].lower()
    if not (prefix.startswith("graph") or prefix.startswith("flowchart")):
        return diagram

    # Build label -> id map only from bare-quoted nodes (not labels inside [...])
    label_to_id: Dict[str, str] = {}
    for line in lines[1:]:
        for match in _MERMAID_BARE_QUOTED_LEFT_RE.finditer(line):
            label = match.group(1)
            if label and label not in label_to_id:
                label_to_id[label] = _label_to_node_id(label)
        for match in _MERMAID_BARE_QUOTED_RIGHT_RE.finditer(line):
            label = match.group(2)
            if label and label not in label_to_id:
                label_to_id[label] = _label_to_node_id(label)

    if not label_to_id:
        return diagram

    def replace_bare_left(match: re.Match) -> str:
        label, arrow = match.group(1), match.group(2)
        node_id = label_to_id.get(label, _label_to_node_id(label))
        escaped_label = label.replace('"', "'")
        return f'{node_id}["{escaped_label}"] {arrow}'

    def replace_bare_right(match: re.Match) -> str:
        edge, label = match.group(1), match.group(2)
        node_id = label_to_id.get(label, _label_to_node_id(label))
        escaped_label = label.replace('"', "'")
        return f'{edge} {node_id}["{escaped_label}"]'

    result_lines = [lines[0]]
    for line in lines[1:]:
        head = line.split(":", 1)[0] if ":" in line else line
        tail = (" " + line.split(":", 1)[1]) if ":" in line else ""
        head = _MERMAID_BARE_QUOTED_LEFT_RE.sub(replace_bare_left, head)
        head = _MERMAID_BARE_QUOTED_RIGHT_RE.sub(replace_bare_right, head)
        result_lines.append(head + tail)
    return "\n".join(result_lines)


def _sanitize_mermaid(diagram: str) -> Optional[str]:
    """Strip markdown code fences and trim. Returns None if empty after sanitization."""
    if not diagram or not isinstance(diagram, str):
        return None
    s = diagram.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    s = s.strip()
    return s if s else None


def _format_mermaid_diagram(diagram: str) -> str:
    """Reformat a Mermaid diagram string into well-structured multi-line syntax
    that front-end parsers (mermaid-js) can reliably render."""
    if not diagram:
        return diagram

    lines = diagram.split("\n")
    if len(lines) == 1 and ";" in diagram:
        parts = [p.strip() for p in diagram.split(";") if p.strip()]
        lines = parts

    output_lines: List[str] = []
    for i, line in enumerate(lines):
        line = line.rstrip(";").strip()
        if not line:
            continue
        if i == 0:
            first_token = line.split()[0].lower()
            is_declaration = any(
                first_token.startswith(p) for p in _MERMAID_DIAGRAM_PREFIXES
            )
            if is_declaration:
                output_lines.append(line)
                continue

        def _quote_label(match: re.Match) -> str:
            bracket_open = match.group(1)
            label = match.group(2)
            bracket_close = match.group(3)
            if _MERMAID_SPECIAL_LABEL_CHARS.search(label) and not label.startswith('"'):
                label = '"' + label.replace('"', "'") + '"'
            return bracket_open + label + bracket_close

        line = re.sub(r"(\[)([^\]]+)(\])", _quote_label, line)
        line = re.sub(r"(\(\()([^)]+)(\)\))", _quote_label, line)
        line = re.sub(r"(\{)([^}]+)(\})", _quote_label, line)

        if line:
            output_lines.append("    " + line)

    result = "\n".join(output_lines)
    return _repair_flowchart_bare_quoted_nodes(result)


def _is_valid_mermaid(diagram: str) -> bool:
    """Check that the string looks like valid Mermaid syntax."""
    if not diagram or len(diagram) > 8000:
        return False
    first_line = diagram.split("\n")[0].strip()
    if not first_line:
        return False
    prefix = first_line.split()[0] if first_line else ""
    if not any(prefix.lower().startswith(p) for p in _MERMAID_DIAGRAM_PREFIXES):
        return False
    if not _MERMAID_EDGE_OR_NODE_RE.search(diagram):
        return False
    return True


def _filter_valid_mermaid_diagrams(diagrams: Optional[List[str]]) -> List[str]:
    """Sanitize, reformat, validate diagrams; return only those that pass."""
    if not diagrams or not isinstance(diagrams, list):
        return []
    result = []
    for item in diagrams:
        if not isinstance(item, str):
            continue
        sanitized = _sanitize_mermaid(item)
        if not sanitized:
            continue
        formatted = _format_mermaid_diagram(sanitized)
        if _is_valid_mermaid(formatted):
            result.append(formatted)
        else:
            logger.debug(
                "Omitting invalid Mermaid diagram (length=%s)",
                len(item) if item else 0,
            )
    return result


def _parse_llm_response(response: str) -> dict:
    """Parse LLM response string into llm_data dict (with unwrap, repair, fallbacks)."""
    llm_data = None
    try:
        code_block_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response)
        if code_block_match:
            try:
                llm_data = json.loads(code_block_match.group(1))
            except json.JSONDecodeError:
                llm_data = _repair_truncated_json(code_block_match.group(1))

        if not llm_data:
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                candidate = json_match.group(0)
                try:
                    llm_data = json.loads(candidate)
                except json.JSONDecodeError:
                    llm_data = _repair_truncated_json(candidate)

        if not llm_data:
            raise ValueError("No valid JSON found in response")

        llm_data = _unwrap_llm_response(llm_data)
        return llm_data

    except Exception:
        clean_summary = response.replace("```json", "").replace("```", "").strip()
        if clean_summary.startswith("{"):
            try:
                llm_data = json.loads(clean_summary)
                llm_data = _unwrap_llm_response(llm_data)
                if llm_data:
                    return llm_data
            except (json.JSONDecodeError, TypeError):
                pass
        llm_data = _extract_inner_json_from_response(clean_summary)
        if llm_data:
            return llm_data
        llm_data = _repair_truncated_json(clean_summary)
        if llm_data:
            return llm_data
        return {
            "summary": clean_summary,
            "key_insights": ["Could not parse structured insights from LLM response."],
            "generated_diagrams": [],
            "structured_sections": {},
        }


def extract_sources_from_output(output: str) -> List[str]:
    """Extract source URLs from crew output"""
    sources = []

    # Pattern to match URLs (more comprehensive)
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]()]+[^\s<>"{}|\\^`\[\]().,;:!?]'
    urls = re.findall(url_pattern, output)

    # Also try to match URLs in markdown links [text](url)
    markdown_link_pattern = r"\[([^\]]+)\]\((https?://[^\)]+)\)"
    markdown_urls = re.findall(markdown_link_pattern, output)
    urls.extend([url for _, url in markdown_urls])

    # Remove duplicates while preserving order
    seen = set()
    for url in urls:
        # Clean up URL (remove trailing punctuation)
        url = url.rstrip(".,;:!?)")
        # Basic URL validation
        if (
            url not in seen
            and len(url) > 10
            and ("http://" in url or "https://" in url)
        ):
            seen.add(url)
            sources.append(url)

    return sources


def run_research_job(job_id: str, topic: str):
    """Run the CrewAI research job in background"""
    try:
        update_job_status(job_id, "running")
        update_job_progress(
            job_id,
            "Initializing research crew",
            10,
            "Setting up AI agents for research",
        )

        from ai_research_backend.crew import AiResearchBackend

        crew_instance = AiResearchBackend()
        crew = crew_instance.crew()

        # Prepare inputs
        inputs = {"topic": topic, "current_year": str(datetime.now().year)}

        update_job_progress(
            job_id,
            "Searching for papers",
            30,
            "AI agents are searching for relevant research papers",
        )

        # Run crew
        result = crew.kickoff(inputs=inputs)

        update_job_progress(
            job_id,
            "Analyzing content",
            70,
            "Analyzing research papers and extracting key insights",
        )

        # Extract report from result
        report = ""
        all_outputs = []

        if hasattr(result, "raw"):
            report = str(result.raw)
            all_outputs.append(report)

        if hasattr(result, "tasks_output"):
            # Get output from all tasks
            for task_output in result.tasks_output:
                task_str = str(task_output)
                all_outputs.append(task_str)
                # Get output from the last task (reporting_task) as main report
                if task_output == result.tasks_output[-1]:
                    report = task_str

        # If we still don't have a report, use the string representation
        if not report:
            report = str(result)
            all_outputs.append(report)

        update_job_progress(
            job_id,
            "Extracting sources and finalizing",
            90,
            "Extracting source citations from research",
        )

        # Extract sources from all outputs
        sources = []
        for output in all_outputs:
            task_sources = extract_sources_from_output(str(output))
            sources.extend(task_sources)

        # Also check if result has task execution details
        if hasattr(result, "tasks"):
            for task in result.tasks:
                if hasattr(task, "output"):
                    task_sources = extract_sources_from_output(str(task.output))
                    sources.extend(task_sources)

        # Remove duplicates while preserving order
        sources = list(dict.fromkeys(sources))

        # Add intermediate finding about sources
        if sources:
            add_intermediate_finding(job_id, f"Found {len(sources)} source citations")

        # Prepare result data
        completed_at = datetime.now().isoformat()
        result_data = {
            "report": report,
            "sources": sources,
            "completed_at": completed_at,
            "jobId": job_id,
            "topic": topic,
        }

        # Save result
        save_result(job_id, result_data)
        update_job_progress(
            job_id, "Completed", 100, "Research report generated successfully"
        )
        update_job_status(job_id, "completed")

    except Exception as e:
        logger.exception("Research job %s failed", job_id)
        completed_at = datetime.now().isoformat()
        result_data = {
            "report": "",
            "sources": [],
            "completed_at": completed_at,
            "jobId": job_id,
            "topic": topic,
            "error": "An internal error occurred while processing the research job.",
        }
        save_result(job_id, result_data)
        update_job_status(job_id, "failed")


@app.post("/api/research", response_model=JobStatusResponse)
@limiter.limit(RATE_LIMIT_STRING)
async def submit_research(
    request: Request,
    body: ResearchRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Submit a new research job"""
    if count_ongoing_jobs() >= MAX_CONCURRENT_JOBS:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Server busy. Try again later.",
                "code": "SERVER_BUSY",
            },
        )
    job_id = create_job(body.topic)

    # Start background task
    background_tasks.add_task(run_research_job, job_id, body.topic)

    return JobStatusResponse(job_id=job_id, status="pending", topic=body.topic)


@app.get("/api/research/{job_id}", response_model=JobStatusResponse)
@limiter.limit(RATE_LIMIT_STRING)
async def get_research_status(
    request: Request,
    job_id: str,
    _: None = Depends(verify_api_key),
):
    """Get the status of a research job"""
    _validate_job_id(job_id)
    if not job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    status = get_job_status(job_id)

    # Get topic from job_topics dict, or from result file if available
    topic = get_job_topic(job_id)
    if not topic:
        result = load_result(job_id)
        topic = result.get("topic", "Unknown") if result else "Unknown"

    # Get progress data
    progress_data = get_job_progress(job_id)

    return JobStatusResponse(
        job_id=job_id,
        status=str(status),
        topic=topic,
        current_step=progress_data.get("current_step") if progress_data else None,
        progress_percentage=(
            progress_data.get("progress_percentage") if progress_data else None
        ),
        chain_of_thought=(
            progress_data.get("chain_of_thought", []) if progress_data else []
        ),
        intermediate_findings=(
            progress_data.get("intermediate_findings", []) if progress_data else []
        ),
    )


@app.get("/api/research/{job_id}/result", response_model=ResearchResultResponse)
@limiter.limit(RATE_LIMIT_STRING)
async def get_research_result(
    request: Request,
    job_id: str,
    _: None = Depends(verify_api_key),
):
    """Get the result of a completed research job"""
    _validate_job_id(job_id)
    if not job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    status = get_job_status(job_id)

    if status == "pending" or status == "running":
        raise HTTPException(
            status_code=400,
            detail=f"Job is still {status}. Please wait for completion.",
        )

    result = load_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    if status == "failed":
        raise HTTPException(
            status_code=500,
            detail="Research job failed. Please try again later.",
        )

    return ResearchResultResponse(
        report=result.get("report", ""),
        sources=result.get("sources", []),
        completed_at=result.get("completed_at", ""),
        jobId=result.get("jobId", job_id),
        topic=result.get("topic", ""),
    )


JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "600"))


def _fail_job(job_id: str, topic: str, reason: str) -> None:
    """Mark a job as failed and persist an error result."""
    logger.error("Job %s failed: %s", job_id, reason)
    result_data = {
        "error": reason,
        "completed_at": datetime.now().isoformat(),
        "jobId": job_id,
        "topic": topic,
    }
    save_result(job_id, result_data)
    update_job_status(job_id, "failed")


def run_dynamic_research_job(job_id: str, topic: str):
    """Run the dynamic research job in background.

    Flow:
      1. Check persistent knowledge base for existing relevant content
      2. If insufficient, download papers from ArXiv and store in knowledge base
      3. Run multi-agent pipeline (analyzer -> synthesis + diagrams in parallel)
      4. Generate visual assets and prepare final result
    """
    import threading

    timed_out = threading.Event()

    def _watchdog():
        timed_out.set()
        logger.error(
            "Job %s exceeded %ds timeout — marking failed", job_id, JOB_TIMEOUT_SECONDS
        )
        _fail_job(job_id, topic, f"Job timed out after {JOB_TIMEOUT_SECONDS}s")

    timer = threading.Timer(JOB_TIMEOUT_SECONDS, _watchdog)
    timer.daemon = True
    timer.start()

    try:
        update_job_status(job_id, "running")
        update_job_progress(
            job_id,
            "Initializing research",
            5,
            "Preparing to research the topic",
        )

        # -- Gate: core modules must have loaded at startup --
        if active_llm is None or run_research_agents is None:
            raise RuntimeError(
                "Core research modules failed to load at startup — "
                "check server logs for import errors"
            )

        # ---- Step 1: Similarity search on existing knowledge base ----
        update_job_progress(
            job_id,
            "Checking knowledge base",
            10,
            "Searching existing embeddings for relevant material",
        )

        existing_context: Optional[str] = None
        if search_existing_knowledge is not None:
            try:
                existing_context = search_existing_knowledge(topic)
            except Exception as exc:
                logger.warning(
                    "Knowledge-base search failed (will download papers): %s", exc
                )

        papers: List[dict] = []
        papers_context: str = ""

        if existing_context:
            add_intermediate_finding(
                job_id,
                "Found sufficient existing knowledge — skipping paper download",
            )
            papers_context = existing_context
            update_job_progress(
                job_id,
                "Using cached knowledge",
                45,
                "Relevant content found in knowledge base, proceeding to analysis",
            )
        else:
            # ---- Step 2: Download new papers from ArXiv ----
            from ai_research_backend.tools.arxiv_tool import ArxivSearchTool

            arxiv_tool = ArxivSearchTool()

            update_job_progress(
                job_id,
                "Searching ArXiv papers",
                20,
                f"Searching for papers related to: {topic}",
            )

            try:
                papers = arxiv_tool.search_papers(topic)
            except Exception as exc:
                logger.warning("ArXiv search failed: %s", exc)
                papers = []

            if papers:
                add_intermediate_finding(
                    job_id, f"Found {len(papers)} relevant research papers"
                )
                update_job_progress(
                    job_id,
                    "Processing paper content",
                    35,
                    f"Analyzing {len(papers)} papers",
                )

            update_job_progress(
                job_id,
                "Embedding papers into knowledge base",
                45,
                "Storing paper chunks and running hybrid retrieval",
            )

            papers_for_context = papers[:7]
            if hybrid_retrieve is not None:
                try:
                    papers_context = hybrid_retrieve(papers_for_context, topic)
                except Exception as exc:
                    logger.warning("Hybrid retrieval failed: %s", exc)
                    papers_context = ""

        if not papers_context and not papers:
            raise RuntimeError(
                "No knowledge-base content and no papers retrieved — "
                "cannot proceed with research"
            )

        # ---- Build section_images_instruction for synthesis agent ----
        available_image_urls: List[str] = []
        available_images_text = ""
        for p in papers:
            p_images = p.get("images", [])
            if p_images:
                available_image_urls.extend(p_images)
                available_images_text += (
                    f'  - "{p.get("title", "Unknown")}": {", ".join(p_images)}\n'
                )

        if available_image_urls:
            section_images_instruction = f"""
            "section_images": {{
                For each section key (overview, key_concepts, benefits, risks, applications, future_directions, methodologies, comparisons, timeline, statistics), provide an array of image URLs from the Available Images list below that best illustrate that section. Use ONLY URLs from this list. Use empty array if none fit.
                Example: "overview": ["/static/extracted_images/paper_p0_i0.png"], "key_concepts": [], ...
            }},

Available Images (use ONLY these URLs in section_images):
{available_images_text}"""
        else:
            section_images_instruction = '"section_images": {},'

        if timed_out.is_set():
            return

        # ---- Step 3: Multi-agent pipeline ----
        update_job_progress(
            job_id,
            "Running research agents",
            55,
            "Paper Analyzer extracting findings, Synthesis + Diagram agents starting",
        )

        llm_data = run_research_agents(
            main_llm=active_llm,
            sub_llm=sub_llm,
            topic=topic,
            papers_context=papers_context,
            section_images_instruction=section_images_instruction,
        )

        if timed_out.is_set():
            return

        logger.info("Multi-agent pipeline completed")

        valid, reason = _validate_llm_response(llm_data)
        if not valid:
            logger.warning(
                "Agent pipeline output validation failed: %s — using raw output", reason
            )

        # ---- Step 4: Post-process results ----
        structured_sections = _parse_structured_sections(
            llm_data.get("structured_sections")
        )

        section_confidence = _normalize_section_confidence(
            llm_data.get("section_confidence")
        )

        allowed_urls = set()
        for p in papers:
            for img_url in p.get("images", []):
                if isinstance(img_url, str):
                    allowed_urls.add(img_url)
        section_images = _normalize_section_images(
            llm_data.get("section_images"), allowed_urls
        )

        update_job_progress(
            job_id,
            "Generating visualizations",
            80,
            "Creating charts and rendering math expressions",
        )

        sections_dict = structured_sections.model_dump()
        if section_images is None:
            section_images = {}
        try:
            from ai_research_backend.section_visuals import (
                render_section_math,
                generate_statistics_chart,
                generate_comparison_chart,
            )

            math_images = render_section_math(sections_dict, job_id)
            for key, urls in math_images.items():
                section_images.setdefault(key, []).extend(urls)

            stats_chart_url = generate_statistics_chart(sections_dict, job_id)
            if stats_chart_url:
                section_images.setdefault("statistics", []).append(stats_chart_url)

            comp_chart_url = generate_comparison_chart(sections_dict, job_id)
            if comp_chart_url:
                section_images.setdefault("comparisons", []).append(comp_chart_url)
        except Exception as e:
            logger.warning("Section visual generation failed (non-fatal): %s", e)

        if timed_out.is_set():
            return

        completed_at = datetime.now().isoformat()

        result_data = {
            "topic": topic,
            "summary": llm_data.get("summary", ""),
            "papers": papers,
            "key_insights": llm_data.get("key_insights", []),
            "generated_diagrams": _filter_valid_mermaid_diagrams(
                llm_data.get("generated_diagrams")
            ),
            "structured_sections": sections_dict,
            "section_confidence": section_confidence,
            "section_images": section_images if section_images else None,
            "completed_at": completed_at,
            "jobId": job_id,
        }

        update_job_progress(
            job_id, "Finalizing results", 95, "Preparing final research output"
        )

        save_result(job_id, result_data)
        update_job_progress(
            job_id, "Completed", 100, "Dynamic research completed successfully"
        )
        update_job_status(job_id, "completed")

    except Exception as e:
        if not timed_out.is_set():
            logger.exception("Dynamic research job %s failed", job_id)
            _fail_job(job_id, topic, "An internal error occurred while processing the research job.")
    finally:
        timer.cancel()


@app.post("/api/research/dynamic", response_model=JobStatusResponse)
@limiter.limit(RATE_LIMIT_STRING)
async def submit_dynamic_research(
    request: Request,
    body: ResearchRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_api_key),
):
    """Submit a new dynamic research job"""
    if count_ongoing_jobs() >= MAX_CONCURRENT_JOBS:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Server busy. Try again later.",
                "code": "SERVER_BUSY",
            },
        )
    job_id = create_job(body.topic)

    # Start background task
    background_tasks.add_task(run_dynamic_research_job, job_id, body.topic)

    return JobStatusResponse(job_id=job_id, status="pending", topic=body.topic)


@app.get(
    "/api/research/dynamic/{job_id}/result",
    response_model=DynamicResearchResultResponse,
)
@limiter.limit(RATE_LIMIT_STRING)
async def get_dynamic_research_result(
    request: Request,
    job_id: str,
    _: None = Depends(verify_api_key),
):
    """Get the result of a completed dynamic research job"""
    _validate_job_id(job_id)
    if not job_exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    status = get_job_status(job_id)

    if status == "pending" or status == "running":
        raise HTTPException(
            status_code=400,
            detail=f"Job is still {status}. Please wait for completion.",
        )

    result = load_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")

    if status == "failed":
        raise HTTPException(
            status_code=500,
            detail="Research job failed. Please try again later.",
        )

    # Build structured_sections from stored dict (backward compatible: missing => empty)
    raw_sections = result.get("structured_sections")
    if isinstance(raw_sections, dict):
        try:
            structured_sections = StructuredSections(**raw_sections)
        except Exception:
            structured_sections = StructuredSections()
    else:
        structured_sections = StructuredSections()

    papers_data = result.get("papers", [])

    raw_section_images = result.get("section_images")
    if isinstance(raw_section_images, dict):
        section_images = {
            k: urls
            for k, urls in raw_section_images.items()
            if isinstance(urls, list)
        }
    else:
        section_images = None

    return DynamicResearchResultResponse(
        topic=result.get("topic", ""),
        summary=result.get("summary", ""),
        papers=papers_data,
        key_insights=result.get("key_insights", []),
        generated_diagrams=result.get("generated_diagrams", []),
        structured_sections=structured_sections,
        section_confidence=result.get("section_confidence"),
        section_images=section_images,
        completed_at=result.get("completed_at", ""),
        jobId=result.get("jobId", job_id),
    )


@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "AI Research Backend API", "version": "1.0.0"}


def main():
    """Run the FastAPI server"""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
