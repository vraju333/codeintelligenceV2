"""Offline contract checks; live retrieval requires project databases."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'services/agent/knowledge_agent_service.py'
TEXT = SOURCE.read_text(encoding='utf-8')

def test_source_parses():
    ast.parse(TEXT)

def test_bare_attribute_mapping_routed():
    assert 'evidence_mapping_{attr}' in TEXT
    assert 'mapping_lineage' in TEXT
    assert 'mapping document' in TEXT

def test_cross_source_requirement_routed():
    assert 'evidence_cross_source_requirements' in TEXT
    assert '["JIRA", "REQUIREMENT", "MAPPING"]' in TEXT

def test_scenario_registry_not_git_proxy():
    assert 'evidence_jira_scenario_registry' in TEXT
    assert 'Never explain missing scenario coverage as caused by uncommitted Git changes' in TEXT

def test_preserves_sequential_tool_execution():
    assert 'sequential_tools_node' in TEXT
