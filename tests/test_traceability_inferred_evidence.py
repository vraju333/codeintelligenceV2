import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "services/knowledge/requirement_traceability_service.py"
def test_syntax():
    ast.parse(SOURCE.read_text())
def test_inferred_coverage_is_not_verified():
    text = SOURCE.read_text()
    assert '"classification": "INFERRED_SCENARIO_CANDIDATE"' in text
    assert '"inferred_candidates": scenario_candidates' in text
    assert 'for item in result["test_baseline_traceability"]["items"]:' in text
def test_call_graph_is_exposed():
    text = SOURCE.read_text()
    assert '"observed_call_edges": observed_calls' in text
    assert 'endpoint_to_methods' in text
def test_no_inferred_implements():
    assert 'MERGE (m)-[r:IMPLEMENTS]' not in SOURCE.read_text()
