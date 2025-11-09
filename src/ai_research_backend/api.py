import re
from datetime import datetime
from typing import List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ai_research_backend.models import (
    ResearchRequest,
    JobStatusResponse,
    ResearchResultResponse,
    ErrorResponse
)
from ai_research_backend.job_manager import (
    create_job,
    update_job_status,
    get_job_status,
    save_result,
    load_result,
    job_exists,
    get_job_topic
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


def extract_sources_from_output(output: str) -> List[str]:
    """Extract source URLs from crew output"""
    sources = []
    
    # Pattern to match URLs (more comprehensive)
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]()]+[^\s<>"{}|\\^`\[\]().,;:!?]'
    urls = re.findall(url_pattern, output)
    
    # Also try to match URLs in markdown links [text](url)
    markdown_link_pattern = r'\[([^\]]+)\]\((https?://[^\)]+)\)'
    markdown_urls = re.findall(markdown_link_pattern, output)
    urls.extend([url for _, url in markdown_urls])
    
    # Remove duplicates while preserving order
    seen = set()
    for url in urls:
        # Clean up URL (remove trailing punctuation)
        url = url.rstrip('.,;:!?)')
        # Basic URL validation
        if url not in seen and len(url) > 10 and ('http://' in url or 'https://' in url):
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
        inputs = {
            'topic': topic,
            'current_year': str(datetime.now().year)
        }
        
        # Run crew
        result = crew.kickoff(inputs=inputs)
        
        # Extract report from result
        report = ""
        all_outputs = []
        
        if hasattr(result, 'raw'):
            report = str(result.raw)
            all_outputs.append(report)
        
        if hasattr(result, 'tasks_output'):
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
        if hasattr(result, 'tasks'):
            for task in result.tasks:
                if hasattr(task, 'output'):
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
            "topic": topic
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
            "error": error_message
        }
        save_result(job_id, result_data)
        update_job_status(job_id, "failed")


@app.post("/api/research", response_model=JobStatusResponse)
async def submit_research(request: ResearchRequest, background_tasks: BackgroundTasks):
    """Submit a new research job"""
    job_id = create_job(request.topic)
    
    # Start background task
    background_tasks.add_task(run_research_job, job_id, request.topic)
    
    return JobStatusResponse(
        job_id=job_id,
        status="pending",
        topic=request.topic
    )


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
    
    return JobStatusResponse(
        job_id=job_id,
        status=status,
        topic=topic
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
            detail=f"Job is still {status}. Please wait for completion."
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
        topic=result.get("topic", "")
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

