"""Offline release guards for Ask evidence routing, document discovery and chat UX."""
import ast
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / 'services/agent/knowledge_agent_service.py'
REGISTRY = ROOT / 'services/agent/knowledge_tool_registry.py'
VERIFY = ROOT / 'services/knowledge/knowledge_hub_verification_service.py'
PLUGIN = ROOT.parent / 'plugin/src/main/kotlin/com/codeintelligence/plugin/CodeIntelligenceToolWindowFactory.kt'

def test_python_syntax():
    for p in (AGENT, REGISTRY, VERIFY):
        ast.parse(p.read_text())

def test_attribute_routing_includes_authoritative_mapping_and_candidate_jira():
    text = AGENT.read_text()
    assert 'mandatory_attribute_mapping_lineage' in text
    assert 'mandatory_attribute_jira_mapping_discovery' in text
    assert 'source_types": ["JIRA", "REQUIREMENT", "MAPPING"]' in text
    assert 'mandatory_attribute_scenario_impact' in text

def test_document_catalog_and_evidence_safety():
    assert 'mandatory_mapping_document_catalog' in AGENT.read_text()
    assert 'def mapping_document_catalog(self)' in REGISTRY.read_text()
    assert 'A verified JIRA document is NOT a verified JIRA-to-class link' in AGENT.read_text()

def test_missing_optional_neo4j_relationships_are_guarded():
    text = VERIFY.read_text()
    assert 'CALL db.relationshipTypes()' in text
    assert 'CALL db.labels()' in text
    assert 'def optional_count(' in text

def test_plugin_preserves_submitted_questions_and_history():
    text = PLUGIN.read_text()
    assert 'private val askHistory = StringBuilder()' in text
    assert 'askHistory.append("YOU\\n")' in text
    assert 'askHistory.append(EngineeringBriefFormatter.agentAnswer(json))' in text
    assert 'askOutput.text = askHistory.toString()' in text
