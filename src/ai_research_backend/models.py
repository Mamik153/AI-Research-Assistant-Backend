from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ResearchRequest(BaseModel):
    """Request model for research job submission"""

    topic: str = Field(..., description="Research topic to investigate")


class JobStatusResponse(BaseModel):
    """Response model for job status"""

    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(
        ..., description="Job status: pending, running, completed, or failed"
    )
    topic: str = Field(..., description="Research topic")


class ResearchResultResponse(BaseModel):
    """Response model for research results"""

    report: str = Field(..., description="Research report in markdown format")
    sources: List[str] = Field(default_factory=list, description="List of source URLs")
    completed_at: str = Field(..., description="ISO format timestamp of completion")
    jobId: str = Field(..., description="Job identifier")
    topic: str = Field(..., description="Research topic")


class ErrorResponse(BaseModel):
    """Error response model"""

    error: str = Field(..., description="Error message")
    job_id: Optional[str] = Field(None, description="Job identifier if available")


class PaperData(BaseModel):
    """Data model for a single research paper"""

    title: str = Field(..., description="Paper title")
    authors: List[str] = Field(..., description="List of authors")
    published: str = Field(..., description="Publication date")
    summary: str = Field(..., description="Paper summary")
    pdf_url: str = Field(..., description="URL to the PDF")
    images: List[str] = Field(
        default_factory=list, description="List of extracted image URLs"
    )


class DynamicResearchResultResponse(BaseModel):
    """Response model for dynamic research results"""

    topic: str = Field(..., description="Research topic")
    summary: str = Field(..., description="Synthesized summary of the research")
    papers: List[PaperData] = Field(..., description="List of papers found")
    key_insights: List[str] = Field(
        ..., description="Key insights extracted from papers"
    )
    generated_diagrams: List[str] = Field(
        default_factory=list, description="List of generated Mermaid diagrams"
    )
    # You can add more fields here like 'suggested_structure' if needed
    completed_at: str = Field(..., description="ISO format timestamp of completion")
    jobId: str = Field(..., description="Job identifier")
