"""Multi-agent pipeline for dynamic research.

Three specialised agents replace the former single monolithic LLM call:

1. Paper Analyzer  (sub_llm) - extracts structured findings from chunks
2. Synthesis Agent (active_llm) - writes narrative + structured sections
3. Diagram Agent   (sub_llm) - generates well-formatted Mermaid diagrams

The Diagram Agent runs concurrently with the Synthesis Agent.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent 1 – Paper Analyzer
# ---------------------------------------------------------------------------

_ANALYZER_PROMPT = """You are a meticulous research analyst. Given a set of paper excerpts about "{topic}", extract the following structured data.

Return valid JSON only (no markdown fences):
{{
    "key_findings": ["finding 1", "finding 2", ...],
    "methodologies": [
        {{"name": "Method", "description": "What it does", "use_cases": ["use case"]}}
    ],
    "statistics": [
        {{"label": "Metric", "value": "number", "context": "where from", "source": "paper title or null"}}
    ],
    "comparisons": {{
        "criteria": ["criterion A", "criterion B"],
        "items": [
            {{"name": "Item 1", "values": ["val A", "val B"]}},
            {{"name": "Item 2", "values": ["val A", "val B"]}}
        ]
    }},
    "timeline_events": [
        {{"period": "year or range", "event": "what happened", "significance": "why it matters"}}
    ],
    "applications": [
        {{"title": "Use case", "description": "Details", "industry": "sector or null"}}
    ],
    "risks": [
        {{"title": "Risk", "description": "Details", "severity": "high" or "medium" or "low"}}
    ]
}}

If a section has no data, use an empty array or null. Focus on accuracy over completeness.

Research Excerpts:
{context}"""


def run_paper_analyzer(llm, topic: str, context: str) -> dict:
    """Agent 1: Extract structured findings from retrieved paper chunks."""
    prompt = _ANALYZER_PROMPT.format(topic=topic, context=context)
    try:
        response = llm.call(messages=[{"role": "user", "content": prompt}])
        return _safe_json_parse(response, fallback_label="paper_analyzer")
    except Exception as e:
        logger.error("Paper Analyzer failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Agent 2 – Synthesis Agent
# ---------------------------------------------------------------------------

_SYNTHESIS_PROMPT = """You are an expert AI researcher explaining "{topic}" to someone who wants to understand it deeply.

You have two sources of information:
A) Paper abstracts and retrieved excerpts
B) Structured findings already extracted by an analyst

Using both, produce a JSON response with this exact structure (no markdown fences):
{{
    "summary": "3-5 paragraph flowing narrative. Do not reference 'Paper 1' or 'Excerpt'. Synthesise ideas with transitions like 'Furthermore', 'Research shows'.",
    "key_insights": ["Insight 1", "Insight 2", ...],
    "structured_sections": {{
        "overview": {{ "title": "Short title", "content": "Brief intro paragraph", "visualization_type": "card" }},
        "key_concepts": [
            {{ "name": "Concept", "description": "What it is", "related_concepts": ["Other concept"] }}
        ],
        "benefits": [
            {{ "title": "Benefit", "description": "What it is", "importance": "high" or "medium" or "low" }}
        ],
        "risks": {risks_json},
        "applications": {applications_json},
        "future_directions": [
            {{ "title": "Trend", "description": "Details", "timeframe": "e.g. Next 5 years" or null }}
        ],
        "methodologies": {methodologies_json},
        "comparisons": {comparisons_json},
        "timeline": {timeline_json},
        "statistics": {statistics_json}
    }},
    "section_confidence": {{
        "overview": 0.0-1.0, "key_concepts": 0.0-1.0, "benefits": 0.0-1.0,
        "risks": 0.0-1.0, "applications": 0.0-1.0, "future_directions": 0.0-1.0,
        "methodologies": 0.0-1.0, "comparisons": 0.0-1.0, "timeline": 0.0-1.0,
        "statistics": 0.0-1.0
    }},
    {section_images_instruction}
}}

Rules:
- Use only valid JSON. No markdown code fences.
- The "summary" must be a single plain-text string, not nested JSON.
- Populate every section the papers support; use empty arrays or null where no data.
- For section_confidence, rate each section 0.0 (no support) to 1.0 (strongly supported).
- For section_images, use ONLY URLs from the Available Images list if one is provided.

Research Material:
{papers_context}

Analyst Findings:
{analyzer_json}"""


def run_synthesis_agent(
    llm,
    topic: str,
    papers_context: str,
    analyzer_output: dict,
    section_images_instruction: str,
) -> dict:
    """Agent 2: Synthesise findings into narrative + structured sections."""

    def _json_or_empty(data, key):
        val = data.get(key)
        if val is None:
            return "null"
        return json.dumps(val)

    prompt = _SYNTHESIS_PROMPT.format(
        topic=topic,
        papers_context=papers_context,
        analyzer_json=json.dumps(analyzer_output, indent=2),
        risks_json=_json_or_empty(analyzer_output, "risks"),
        applications_json=_json_or_empty(analyzer_output, "applications"),
        methodologies_json=_json_or_empty(analyzer_output, "methodologies"),
        comparisons_json=_json_or_empty(analyzer_output, "comparisons"),
        timeline_json=_json_or_empty(analyzer_output, "timeline_events"),
        statistics_json=_json_or_empty(analyzer_output, "statistics"),
        section_images_instruction=section_images_instruction,
    )
    try:
        response = llm.call(messages=[{"role": "user", "content": prompt}])
        return _safe_json_parse(response, fallback_label="synthesis_agent")
    except Exception as e:
        logger.error("Synthesis Agent failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Agent 3 – Diagram Agent
# ---------------------------------------------------------------------------

_DIAGRAM_PROMPT = """You are a Mermaid diagram specialist. Given key findings about "{topic}", generate 1-3 Mermaid diagrams that visualise the most important relationships, processes, or architectures.

STRICT FORMAT RULES:
- Return valid JSON only: {{ "diagrams": ["diagram1", "diagram2"] }}
- Each diagram is a string using MULTI-LINE format with literal newline characters (\\n).
- First line MUST be the diagram type declaration (e.g. "graph TD", "flowchart LR", "sequenceDiagram").
- Each subsequent statement on its own line, indented with 4 spaces.
- Node IDs must NOT contain spaces. Use camelCase: InputData, not Input Data.
- Node labels containing special characters (parentheses, colons, ampersands) MUST be wrapped in double quotes.
- Do NOT use semicolons to separate statements.
- Do NOT wrap in markdown code fences.

GOOD example:
"graph TD\\n    A[\\"Data Collection\\"] --> B[\\"Preprocessing\\"]\\n    B --> C[\\"Model Training\\"]\\n    C --> D[\\"Evaluation\\"]"

BAD examples (never do these):
- "graph TD; A --> B; B --> C"  (semicolons)
- "graph TD A --> B"  (missing newlines)
- "Input Node --> Output Node"  (spaces in IDs)

Key Findings:
{findings}"""


def run_diagram_agent(llm, topic: str, findings: List[str]) -> List[str]:
    """Agent 3: Generate well-formatted Mermaid diagrams."""
    findings_text = "\n".join(f"- {f}" for f in findings[:15])
    prompt = _DIAGRAM_PROMPT.format(topic=topic, findings=findings_text)
    try:
        response = llm.call(messages=[{"role": "user", "content": prompt}])
        data = _safe_json_parse(response, fallback_label="diagram_agent")
        diagrams = data.get("diagrams", [])
        if isinstance(diagrams, list):
            return [d for d in diagrams if isinstance(d, str) and d.strip()]
        return []
    except Exception as e:
        logger.error("Diagram Agent failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_research_agents(
    main_llm,
    sub_llm,
    topic: str,
    papers_context: str,
    section_images_instruction: str,
) -> dict:
    """Run the full 3-agent pipeline, returning a combined result dict.

    Flow:
      1. Paper Analyzer (sub_llm) extracts structured findings
      2. In parallel:
         a. Synthesis Agent (main_llm) writes narrative + sections
         b. Diagram Agent (sub_llm) generates Mermaid diagrams
      3. Merge results
    """
    logger.info("Running Paper Analyzer agent")
    analyzer_output = run_paper_analyzer(sub_llm, topic, papers_context)

    findings = analyzer_output.get("key_findings", [])
    if not findings:
        findings = [f"Research topic: {topic}"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        logger.info("Running Synthesis + Diagram agents in parallel")
        synthesis_future: Future = pool.submit(
            run_synthesis_agent,
            main_llm,
            topic,
            papers_context,
            analyzer_output,
            section_images_instruction,
        )
        diagram_future: Future = pool.submit(
            run_diagram_agent, sub_llm, topic, findings
        )

        synthesis_result = synthesis_future.result()
        diagram_result = diagram_future.result()

    synthesis_result["generated_diagrams"] = diagram_result
    return synthesis_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_json_parse(response: str, fallback_label: str = "agent") -> dict:
    """Attempt to parse LLM response as JSON with basic repair."""
    if not response:
        return {}
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse %s JSON response, attempting repair", fallback_label)
        try:
            import json_repair  # type: ignore[import-untyped]
            return json_repair.loads(text)
        except Exception:
            pass
        logger.error("Could not parse %s response at all", fallback_label)
        return {}
