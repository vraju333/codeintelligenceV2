"""Regression guards for consolidated single-project question routing."""
import ast
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[1] / 'routers' / 'knowledge_agent_router.py'


def _functions():
    module = ast.parse(ROUTER.read_text(encoding='utf-8'))
    return {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}


def test_explicit_intelligence_router_precedes_legacy_routes():
    source = ROUTER.read_text(encoding='utf-8')
    start = source.index('def _ask_knowledge_agent_impl(')
    body = source[start:]
    assert body.index('if requested_attributes and _intelligence_intent(query):') < body.index('requested = _requested_evidence(query)')


def test_multi_attribute_isolated_aggregation():
    functions = _functions()
    assert '_multi_attribute_intelligence' in functions
    body = ast.get_source_segment(ROUTER.read_text(), functions['_multi_attribute_intelligence'])
    assert 'attribute_analyses' in body
    assert 'NO_CROSS_ATTRIBUTE_VERIFICATION_INFERENCE' in body


def test_regression_recommendations_not_executions():
    functions = _functions()
    body = ast.get_source_segment(ROUTER.read_text(), functions['_enrich_intelligence'])
    assert 'NOT_VERIFIED' in body
    assert 'IMPORTED_QA_PLAN_CANDIDATE' in body
