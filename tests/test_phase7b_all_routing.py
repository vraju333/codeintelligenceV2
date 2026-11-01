"""No DB required: assert Phase 7B intents outrank historical evidence fallback."""
import ast
from pathlib import Path

ROUTER = Path(__file__).resolve().parents[1] / 'routers' / 'knowledge_agent_router.py'
module = ast.parse(ROUTER.read_text(encoding='utf-8'))
functions = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in ('_intelligence_intent', '_requested_attributes')]
namespace = {'re': __import__('re')}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROUTER), 'exec'), namespace)


def test_all_failed_queries_route_to_intelligence():
    for query in (
        'Show the Java AST call path for accountType from PATCH endpoint to service.',
        'Show all conditional validation branches for countryCode',
        'Which methods actually modify accountType versus only read it?',
        'Show verified and unverified impact relationships for countryCode.',
    ):
        assert namespace['_intelligence_intent'](query), query
        assert len(namespace['_requested_attributes'](query)) == 1, query


def test_url_placeholder_is_not_an_attribute():
    query = 'Does changing countryCode impact PATCH /api/customers/{customerId}/account-type?'
    assert namespace['_requested_attributes'](query) == ['countryCode']
