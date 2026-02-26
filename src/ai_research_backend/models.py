from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# --- Structured sections for UI-friendly dynamic research ---


class SectionContent(BaseModel):
    """Base model for section data with title, content, and visualization type."""

    title: str = Field(..., description="Section title")
    content: str = Field(..., description="Section content")
    visualization_type: str = Field(
        default="card", description="Suggested UI component: card, list, etc."
    )


class KeyConcept(BaseModel):
    """Model for key concepts with name, description, and relationships."""

    name: str = Field(..., description="Concept name")
    description: str = Field(..., description="Concept description")
    related_concepts: List[str] = Field(
        default_factory=list, description="Related concept names for network graph"
    )


class BenefitItem(BaseModel):
    """Model for a single benefit item."""

    title: str = Field(..., description="Benefit title")
    description: str = Field(..., description="Benefit description")
    importance: str = Field(
        default="medium",
        description="Importance level: high, medium, or low",
    )


class RiskItem(BaseModel):
    """Model for a single risk or challenge item."""

    title: str = Field(..., description="Risk title")
    description: str = Field(..., description="Risk description")
    severity: str = Field(
        default="medium",
        description="Severity level: high, medium, or low",
    )


class ApplicationItem(BaseModel):
    """Model for an application or use case."""

    title: str = Field(..., description="Application title")
    description: str = Field(..., description="Application description")
    industry: Optional[str] = Field(None, description="Relevant industry or domain")


class FutureDirectionItem(BaseModel):
    """Model for a future direction or trend."""

    title: str = Field(..., description="Direction title")
    description: str = Field(..., description="Direction description")
    timeframe: Optional[str] = Field(None, description="Expected timeframe if known")


class MethodologyItem(BaseModel):
    """Model for a methodology or approach."""

    name: str = Field(..., description="Methodology name")
    description: str = Field(..., description="Methodology description")
    use_cases: List[str] = Field(
        default_factory=list, description="Example use cases",
    )


class ComparisonRow(BaseModel):
    """Single row in a comparison table."""

    name: str = Field(..., description="Row label / item name")
    values: List[str] = Field(..., description="Values for each criterion column")


class ComparisonData(BaseModel):
    """Model for comparison table data."""

    criteria: List[str] = Field(..., description="Column criteria for comparison")
    items: List[ComparisonRow] = Field(
        ...,
        description="Rows: each item has name and values matching criteria",
    )


class TimelineEvent(BaseModel):
    """Model for a timeline event."""

    period: str = Field(..., description="Date or period label")
    event: str = Field(..., description="Event description")
    significance: Optional[str] = Field(None, description="Why this matters")


class MetricData(BaseModel):
    """Model for statistics or metrics."""

    label: str = Field(..., description="Metric label")
    value: str = Field(..., description="Metric value (can be number or text)")
    context: Optional[str] = Field(None, description="Additional context")
    source: Optional[str] = Field(None, description="Source of the statistic")


class StructuredSections(BaseModel):
    """Container model for all structured research sections."""

    overview: Optional[SectionContent] = Field(
        None, description="Overview section for hero/card"
    )
    key_concepts: List[KeyConcept] = Field(
        default_factory=list, description="Key concepts for network graph"
    )
    benefits: List[BenefitItem] = Field(
        default_factory=list, description="Benefits for cards/lists"
    )
    risks: List[RiskItem] = Field(
        default_factory=list, description="Risks/challenges for cards/lists"
    )
    applications: List[ApplicationItem] = Field(
        default_factory=list, description="Applications for cards"
    )
    future_directions: List[FutureDirectionItem] = Field(
        default_factory=list, description="Future directions for roadmap/timeline"
    )
    methodologies: List[MethodologyItem] = Field(
        default_factory=list, description="Methodologies for cards/lists"
    )
    comparisons: Optional[ComparisonData] = Field(
        None, description="Comparison table data"
    )
    timeline: List[TimelineEvent] = Field(
        default_factory=list, description="Timeline events"
    )
    statistics: List[MetricData] = Field(
        default_factory=list, description="Statistics for metric cards"
    )


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
    current_step: Optional[str] = Field(
        None, description="Current step/phase being executed"
    )
    progress_percentage: Optional[int] = Field(
        None, description="Progress percentage (0-100)"
    )
    chain_of_thought: Optional[List[str]] = Field(
        default_factory=list, description="Chain of thought messages showing progress"
    )
    intermediate_findings: Optional[List[str]] = Field(
        default_factory=list, description="Intermediate findings discovered so far"
    )


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
    structured_sections: Optional[StructuredSections] = Field(
        default_factory=StructuredSections,
        description="UI-friendly structured sections for cards, graphs, timelines",
    )
    completed_at: str = Field(..., description="ISO format timestamp of completion")
    jobId: str = Field(..., description="Job identifier")
