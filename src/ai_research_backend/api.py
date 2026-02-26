import json
import logging
import re
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os
import asyncio
import shutil

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
)
from ai_research_backend.crew import AiResearchBackend

logger = logging.getLogger(__name__)

app = FastAPI(title="AI Research Backend API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

# Mount static directory for serving images
static_dir = os.path.join(os.getcwd(), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

async def delayed_cleanup(delay_seconds: int = 3600):
    """
    Wait for the specified delay, then delete all files in the results and static/extracted_images directories.
    """
    await asyncio.sleep(delay_seconds)
    logger.info("Starting delayed cleanup of results and extracted images...")
    
    # Paths to clean
    results_dir = os.path.join(os.getcwd(), "results")
    extracted_images_dir = os.path.join(os.getcwd(), "static", "extracted_images")
    
    # Clean results directory
    if os.path.exists(results_dir):
        for filename in os.listdir(results_dir):
            file_path = os.path.join(results_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.error(f"Failed to delete {file_path}. Reason: {e}")
                
    # Clean extracted images directory
    if os.path.exists(extracted_images_dir):
        for filename in os.listdir(extracted_images_dir):
            file_path = os.path.join(extracted_images_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                logger.error(f"Failed to delete {file_path}. Reason: {e}")
                
    logger.info("Delayed cleanup completed.")


def _parse_structured_sections(data: Optional[dict]) -> StructuredSections:
    """Parse raw dict from LLM into StructuredSections; return empty on failure."""
    if not data or not isinstance(data, dict):
        return StructuredSections()
    try:
        return StructuredSections(**data)
    except Exception:
        return StructuredSections()


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
        if any(k in parsed for k in ("key_insights", "structured_sections", "generated_diagrams")):
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
                            k in candidate for k in ("summary", "key_insights", "structured_sections")
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


def _parse_llm_response(response: str) -> dict:
    """Parse LLM response string into llm_data dict (with unwrap, repair, fallbacks)."""
    llm_data = None
    try:
        code_block_match = re.search(
            r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response
        )
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

        # Initialize crew
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
        # Handle errors
        error_message = str(e)
        completed_at = datetime.now().isoformat()
        result_data = {
            "report": "",
            "sources": [],
            "completed_at": completed_at,
            "jobId": job_id,
            "topic": topic,
            "error": error_message,
        }
        save_result(job_id, result_data)
        update_job_status(job_id, "failed")


@app.post("/api/research", response_model=JobStatusResponse)
async def submit_research(request: ResearchRequest, background_tasks: BackgroundTasks):
    """Submit a new research job"""
    job_id = create_job(request.topic)

    # Start background task
    background_tasks.add_task(run_research_job, job_id, request.topic)

    return JobStatusResponse(job_id=job_id, status="pending", topic=request.topic)


@app.get("/api/research/{job_id}", response_model=JobStatusResponse)
async def get_research_status(job_id: str):
    """Get the status of a research job"""
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
async def get_research_result(job_id: str):
    """Get the result of a completed research job"""
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
        error_msg = result.get("error", "Unknown error occurred")
        raise HTTPException(status_code=500, detail=error_msg)

    return ResearchResultResponse(
        report=result.get("report", ""),
        sources=result.get("sources", []),
        completed_at=result.get("completed_at", ""),
        jobId=result.get("jobId", job_id),
        topic=result.get("topic", ""),
    )


def run_dynamic_research_job(job_id: str, topic: str):
    """Run the dynamic research job in background"""
    try:
        update_job_status(job_id, "running")
        update_job_progress(
            job_id,
            "Initializing search",
            5,
            "Preparing to search ArXiv for research papers",
        )

        # Initialize tool
        from ai_research_backend.tools.arxiv_tool import ArxivSearchTool

        arxiv_tool = ArxivSearchTool()

        # 1. Search Papers
        update_job_progress(
            job_id,
            "Searching ArXiv papers",
            20,
            f"Searching for papers related to: {topic}",
        )
        papers = arxiv_tool.search_papers(topic)

        # Add intermediate finding about papers found
        if papers:
            add_intermediate_finding(
                job_id, f"Found {len(papers)} relevant research papers"
            )
            update_job_progress(
                job_id,
                "Processing paper content",
                40,
                f"Analyzing {len(papers)} papers",
            )

        # 2. Synthesize with LLM
        from ai_research_backend.crew import active_llm

        # Prepare context from papers (use top 7 to reduce input size; 600 chars per content excerpt)
        papers_for_context = papers[:7]
        papers_context = ""
        for i, p in enumerate(papers_for_context, 1):
            content = (p.get("content") or "")[:600]
            papers_context += f"Paper {i}: {p['title']}\nSummary: {p['summary']}\nContent: {content}...\n\n"

        prompt = f"""
        You are an expert AI researcher explaining the topic of "{topic}" to someone who wants to understand it deeply.
        
        Based on the research papers provided below:
        1. Write a comprehensive, naturally-flowing summary (no "Paper 1 says..."; synthesize ideas).
        2. Extract key insights and create at least 1 Mermaid diagram.
        3. Fill in the "structured_sections" object so the frontend can render cards, graphs, timelines, and tables.
        
        SUMMARY GUIDELINES:
        - Flowing narrative; do not mention "Paper 1", "Paper 2". Use transitions like "Furthermore", "Research shows that".
        - 3-5 detailed paragraphs. Be educational and engaging.
        
        Return valid JSON only, with this exact structure (omit a section's key or use empty array/object if no data):
        {{
            "summary": "Your comprehensive narrative summary (3-5 paragraphs)...",
            "key_insights": ["Insight 1", "Insight 2", ...],
            "generated_diagrams": ["graph TD; A[Concept] --> B[Result];"],
            "structured_sections": {{
                "overview": {{ "title": "Short section title", "content": "Brief intro paragraph", "visualization_type": "card" }},
                "key_concepts": [
                    {{ "name": "Concept name", "description": "What it is", "related_concepts": ["Other concept", ...] }}
                ],
                "benefits": [
                    {{ "title": "Benefit title", "description": "What it is", "importance": "high" or "medium" or "low" }}
                ],
                "risks": [
                    {{ "title": "Risk title", "description": "What it is", "severity": "high" or "medium" or "low" }}
                ],
                "applications": [
                    {{ "title": "Use case title", "description": "What it is", "industry": "e.g. Healthcare" or null }}
                ],
                "future_directions": [
                    {{ "title": "Trend title", "description": "What it is", "timeframe": "e.g. Next 5 years" or null }}
                ],
                "methodologies": [
                    {{ "name": "Method name", "description": "What it is", "use_cases": ["Use case 1", ...] }}
                ],
                "comparisons": {{
                    "criteria": ["Criterion A", "Criterion B", ...],
                    "items": [
                        {{ "name": "Item 1", "values": ["value A", "value B", ...] }},
                        {{ "name": "Item 2", "values": ["value A", "value B", ...] }}
                    ]
                }} or null if no comparison fits,
                "timeline": [
                    {{ "period": "e.g. 2020", "event": "What happened", "significance": "Why it matters" or null }}
                ],
                "statistics": [
                    {{ "label": "Metric name", "value": "e.g. 2,500+", "context": "e.g. Published in 2025", "source": "e.g. ArXiv" or null }}
                ]
            }}
        }}
        
        Rules: Use only valid JSON. No markdown code fences. For generated_diagrams use Mermaid strings only (e.g. "graph TD; A --> B;"). Populate every section that the papers support; use empty arrays or null where no data. IMPORTANT: The "summary" field must be a single plain-text string (the narrative paragraphs), not a nested JSON object.
        
        Research Papers:
        {papers_context}
        """

        update_job_progress(
            job_id,
            "Synthesizing findings with LLM",
            60,
            "AI is analyzing papers and generating insights",
        )

        # Call LLM and parse
        response = active_llm.call(messages=[{"role": "user", "content": prompt}])
        logger.info(
            "LLM response length: %s chars, estimated tokens: ~%s",
            len(response),
            len(response) // 4,
        )
        llm_data = _parse_llm_response(response)
        valid, reason = _validate_llm_response(llm_data)
        if not valid:
            logger.warning("LLM response validation failed: %s", reason)
            # Single retry with simplified prompt (fewer sections to reduce output size)
            simplified_prompt = f"""
        Topic: "{topic}". Based on these research papers, return valid JSON only (no markdown fences). The "summary" must be plain text, not nested JSON.
        {{
            "summary": "3-5 paragraph narrative summary synthesizing the papers...",
            "key_insights": ["Insight 1", "Insight 2", ...],
            "generated_diagrams": ["graph TD; A[Concept] --> B[Result];"],
            "structured_sections": {{
                "overview": {{ "title": "Title", "content": "Brief intro", "visualization_type": "card" }},
                "key_concepts": [{{ "name": "...", "description": "...", "related_concepts": [] }}],
                "benefits": [{{ "title": "...", "description": "...", "importance": "high" or "medium" or "low" }}],
                "risks": [{{ "title": "...", "description": "...", "severity": "high" or "medium" or "low" }}],
                "applications": [],
                "future_directions": [],
                "methodologies": [],
                "comparisons": null,
                "timeline": [],
                "statistics": []
            }}
        }}
        Research Papers:
        {papers_context}
        """
            response2 = active_llm.call(
                messages=[{"role": "user", "content": simplified_prompt}]
            )
            llm_data2 = _parse_llm_response(response2)
            valid2, _ = _validate_llm_response(llm_data2)
            if valid2:
                llm_data = llm_data2
                logger.info("Retry with simplified prompt succeeded")
            else:
                logger.warning(
                    "Retry with simplified prompt still invalid, using first attempt"
                )
        else:
            logger.info("LLM response validation passed")

        # Parse structured_sections from LLM output into validated model, then store as dict
        structured_sections = _parse_structured_sections(llm_data.get("structured_sections"))

        update_job_progress(
            job_id,
            "Generating insights and diagrams",
            80,
            "Creating visualizations and extracting key insights",
        )

        # Prepare result
        completed_at = datetime.now().isoformat()

        # Ensure base URL for images if needed, but relative paths are fine for now as we mount /static
        # The frontend should handle the base URL.

        result_data = {
            "topic": topic,
            "summary": llm_data.get("summary", ""),
            "papers": papers,
            "key_insights": llm_data.get("key_insights", []),
            "generated_diagrams": llm_data.get("generated_diagrams", []),
            "structured_sections": structured_sections.model_dump(),
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
        error_message = str(e)
        completed_at = datetime.now().isoformat()
        result_data = {
            "error": error_message,
            "completed_at": completed_at,
            "jobId": job_id,
            "topic": topic,
        }
        save_result(job_id, result_data)
        update_job_status(job_id, "failed")


@app.post("/api/research/dynamic", response_model=JobStatusResponse)
async def submit_dynamic_research(
    request: ResearchRequest, background_tasks: BackgroundTasks
):
    """Submit a new dynamic research job"""
    job_id = create_job(request.topic)

    # Start background task
    background_tasks.add_task(run_dynamic_research_job, job_id, request.topic)

    return JobStatusResponse(job_id=job_id, status="pending", topic=request.topic)


@app.get(
    "/api/research/dynamic/{job_id}/result",
    response_model=DynamicResearchResultResponse,
)
async def get_dynamic_research_result(job_id: str, background_tasks: BackgroundTasks):
    """Get the result of a completed dynamic research job"""
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
        error_msg = result.get("error", "Unknown error occurred")
        raise HTTPException(status_code=500, detail=error_msg)

    # Build structured_sections from stored dict (backward compatible: missing => empty)
    raw_sections = result.get("structured_sections")
    if isinstance(raw_sections, dict):
        try:
            structured_sections = StructuredSections(**raw_sections)
        except Exception:
            structured_sections = StructuredSections()
    else:
        structured_sections = StructuredSections()

    # Schedule delayed cleanup after returning the result
    # Delay is set to 3600 seconds (1 hour)
    background_tasks.add_task(delayed_cleanup, 3600)

    return DynamicResearchResultResponse(
        topic=result.get("topic", ""),
        summary=result.get("summary", ""),
        papers=result.get("papers", []),
        key_insights=result.get("key_insights", []),
        generated_diagrams=result.get("generated_diagrams", []),
        structured_sections=structured_sections,
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
