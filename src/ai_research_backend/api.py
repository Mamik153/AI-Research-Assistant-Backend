import json
import re
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import os

from ai_research_backend.models import (
    ResearchRequest,
    JobStatusResponse,
    ResearchResultResponse,
    DynamicResearchResultResponse,
    ErrorResponse,
)
from ai_research_backend.job_manager import (
    create_job,
    update_job_status,
    get_job_status,
    save_result,
    load_result,
    job_exists,
    get_job_topic,
)
from ai_research_backend.crew import AiResearchBackend

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

        # Initialize crew
        crew_instance = AiResearchBackend()
        crew = crew_instance.crew()

        # Prepare inputs
        inputs = {"topic": topic, "current_year": str(datetime.now().year)}

        # Run crew
        result = crew.kickoff(inputs=inputs)

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

    return JobStatusResponse(job_id=job_id, status=str(status), topic=topic)


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

        # Initialize tool
        from ai_research_backend.tools.arxiv_tool import ArxivSearchTool

        arxiv_tool = ArxivSearchTool()

        # 1. Search Papers
        papers = arxiv_tool.search_papers(topic)

        # 2. Synthesize with LLM
        from ai_research_backend.crew import groq_llm

        # Prepare context from papers
        papers_context = ""
        for i, p in enumerate(papers, 1):
            papers_context += f"Paper {i}: {p['title']}\nSummary: {p['summary']}\nContent: {p['content'][:1000]}...\n\n"

        prompt = f"""
        Research Topic: {topic}
        
        Based on the provided papers, please generate a response with the following components:

        1.  **Detailed Summary**: A comprehensive and detailed summary of the research findings. Do not be brief; explain the concepts, methodologies, and results in depth.
        2.  **Key Insights**: A list of significant insights or takeaways.
        3.  **Generated Diagrams**: You MUST create at least 1 Mermaid.js diagram definition that visualizes the key concepts.
            - Format the diagram code as a single string.
            - Example: "graph TD; A[Concept] --> B[Result];"
            - Do not include markdown code fence blocks like ```mermaid inside the JSON string value.
        
        Return the response in valid JSON format with the following structure:
        {{
            "summary": "Detailed summary content...",
            "key_insights": ["Insight 1", "Insight 2", ...],
            "generated_diagrams": ["graph TD; ..."]
        }}
        
        Papers:
        {papers_context}
        """

        # Call LLM
        response = groq_llm.call(messages=[{"role": "user", "content": prompt}])

        # Parse JSON from response
        llm_data = None
        try:
            # 1. Try to extract from code blocks first (most reliable)
            code_block_match = re.search(
                r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", response
            )
            if code_block_match:
                try:
                    llm_data = json.loads(code_block_match.group(1))
                except json.JSONDecodeError:
                    pass

            # 2. If no code block match or parsing failed, try finding outermost braces
            if not llm_data:
                # Find first { and last }
                json_match = re.search(r"\{[\s\S]*\}", response)
                if json_match:
                    try:
                        llm_data = json.loads(json_match.group(0))
                    except json.JSONDecodeError:
                        pass

            if not llm_data:
                raise ValueError("No valid JSON found in response")

        except Exception as e:
            print(f"JSON Parsing Warning: {e}")
            # Fallback: Clean up the raw response to use as summary
            clean_summary = response.replace("```json", "").replace("```", "").strip()
            llm_data = {
                "summary": clean_summary,
                "key_insights": [
                    "Could not parse structured insights from LLM response."
                ],
                "generated_diagrams": [],
            }

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
            "completed_at": completed_at,
            "jobId": job_id,
        }

        save_result(job_id, result_data)
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
async def get_dynamic_research_result(job_id: str):
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

    return DynamicResearchResultResponse(
        topic=result.get("topic", ""),
        summary=result.get("summary", ""),
        papers=result.get("papers", []),
        key_insights=result.get("key_insights", []),
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
