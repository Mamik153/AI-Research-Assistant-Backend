"""Regression tests for Mermaid diagram generation and repair.

Ensures flowcharts/graphs use NodeID[\"Label\"] (no bare quoted nodes)
and that sequenceDiagram is not altered by the flowchart repair step.
"""

import pytest

# Import via package so tests run from repo root (e.g. uv run pytest tests/)
from ai_research_backend.api import (
    _format_mermaid_diagram,
    _filter_valid_mermaid_diagrams,
)


def test_repair_bare_quoted_node_in_flowchart():
    """Bare quoted node \"Label\" --> X must become Id[\"Label\"] --> X."""
    broken = '''flowchart LR
    "Hallucination Mitigation" --> J["Multi-Modal Verification"]
'''
    repaired = _format_mermaid_diagram(broken)
    assert 'HallucinationMitigation["' in repaired
    assert '"Hallucination Mitigation" -->' not in repaired or 'HallucinationMitigation["' in repaired


def test_repair_bare_quoted_nodes_in_graph_td():
    """Bare quoted nodes in graph TD must get Id[\"Label\"] form."""
    broken = '''graph TD
    "Knowledge Component Discovery" --> A["SLM-Based Extraction"]
    A --> B["Critical Challenges"]
'''
    repaired = _format_mermaid_diagram(broken)
    assert 'KnowledgeComponentDiscovery["' in repaired


def test_do_not_replace_labels_inside_brackets():
    """Valid nodes A[\"Label\"] must not be altered by repair."""
    diagram = '''flowchart LR
    A["Computational Efficiency"] --> B["Accessibility"]
    "Hallucination Mitigation" --> J["Multi-Modal Verification"]
'''
    repaired = _format_mermaid_diagram(diagram)
    assert 'A["Computational Efficiency"]' in repaired
    assert 'B["Accessibility"]' in repaired
    assert 'A[ComputationalEfficiency[' not in repaired
    assert 'B[Accessibility[' not in repaired
    assert 'HallucinationMitigation["' in repaired


def test_sequence_diagram_unchanged():
    """sequenceDiagram must not be modified by flowchart repair."""
    seq = '''sequenceDiagram
    participant U as "User"
    U ->> S: Request
'''
    out = _format_mermaid_diagram(seq)
    assert out.strip().startswith("sequenceDiagram")
    assert "participant U" in out


def test_filter_valid_accepts_repaired_diagrams():
    """Repaired flowchart and graph pass _filter_valid_mermaid_diagrams."""
    broken_flowchart = '''flowchart LR
    "Hallucination Mitigation" --> J["Multi-Modal Verification"]
'''
    broken_graph = '''graph TD
    "Knowledge Component Discovery" --> A["SLM-Based Extraction"]
'''
    seq = '''sequenceDiagram
    participant U as "User"
    U ->> S: Request
'''
    filtered = _filter_valid_mermaid_diagrams(
        [broken_flowchart, broken_graph, seq]
    )
    assert len(filtered) == 3
