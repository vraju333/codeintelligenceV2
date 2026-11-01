import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load_function(path, name):
    source = path.read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {'re': re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]

def test_direct_impact_routing():
    path = ROOT / 'routers' / 'knowledge_agent_router.py'
    intent = load_function(path, '_intelligence_intent')
    attributes = load_function(path, '_requested_attributes')
    question = 'Does changing countryCode impact PATCH /api/customers/{customerId}/account-type? Show the exact Java call path and evidence.'
    assert intent(question)
    assert attributes(question) == ['countryCode']

def test_field_sensitive_guard_present():
    source = (ROOT / 'services' / 'agent' / 'mapping_contract_flow_service.py').read_text()
    assert '_conflicting_field_update' in source
    assert 'field_name.casefold() != attribute.casefold()' in source
