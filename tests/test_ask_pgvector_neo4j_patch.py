from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    source = (ROOT / path).read_text(encoding='utf-8')
    ast.parse(source)
    return source

def test_missing_neo4j_types_guarded():
    source = read('services/knowledge/knowledge_hub_verification_service.py')
    assert 'CALL db.relationshipTypes()' in source
    assert 'CALL db.labels()' in source
    assert 'required_labels={"ScenarioEvidence"}' in source
    assert 'set(required_types).intersection(present_types)' in source

def test_class_origin_routes():
    source = read('services/agent/knowledge_agent_service.py')
    assert 'mandatory_class_origin_knowledge' in source
    assert 'mandatory_class_origin_graph' in source
    assert 'mandatory_class_origin_pgvector' in source
    assert 'Repository|Request|Entity|Model|Dto|DTO' in source

def test_ask_rag_search_uses_pgvector():
    source = read('services/agent/knowledge_tool_registry.py')
    start = source.index('    def rag_search(')
    end = source.index('    def graph_search(', start)
    block = source[start:end]
    assert 'EnterpriseHybridRagService().search(' in block
    assert 'self.knowledge.search(' not in block
