from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ResearchRequest(BaseModel):
    """Request model for research job submission"""
    topic: str = Field(..., description="Research topic to investigate")


class JobStatusResponse(BaseModel):
    """Response model for job status"""
    job_id: str = Field(..., description="Unique job identifier")
    status: str = Field(..., description="Job status: pending, running, completed, or failed")
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

