"""Standalone regression checks: python tests/test_operation_scope_regressions.py"""
import ast
import re
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
module = ast.parse(source.read_text(encoding='utf-8'))
fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == '_operation_change_scope')
namespace = {'re': re, '_endpoint_targets_attribute': lambda endpoint, method, attrs: any(a.lower() in (endpoint + method).lower() for a in attrs)}
exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), namespace)
scope = namespace['_operation_change_scope']

def check(method, requirement, unresolved, expected_attrs, expected_scope):
    actual = scope({'http_method': method, 'endpoint': '/api/customers', 'controller_method': 'get'}, ['countryCode'], requirement, unresolved)
    assert actual == (expected_attrs, expected_scope), (method, actual)

validation = 'Change countryCode validation to accept only IN, US and GB and reject CA and FR.'
check('GET', validation, False, [], 'REGRESSION_ONLY_OR_UNRELATED')
check('POST', validation, False, ['countryCode'], 'PROPOSED_IMPLEMENTATION')
check('PUT', validation, False, ['countryCode'], 'PROPOSED_IMPLEMENTATION')
check('DELETE', validation, False, [], 'REGRESSION_ONLY_OR_UNRELATED')
check('GET', 'Add customerTier and return it in retrieval responses.', False, ['countryCode'], 'PROPOSED_IMPLEMENTATION')
check('POST', 'For countryCode accept CA and FR but reject CA and FR.', True, [], 'PROVISIONAL_PENDING_CLARIFICATION')
check('PUT', 'For countryCode accept CA and FR but reject CA and FR.', True, [], 'PROVISIONAL_PENDING_CLARIFICATION')
check('GET', 'For countryCode accept CA and FR but reject CA and FR.', True, [], 'PROVISIONAL_PENDING_CLARIFICATION')
print('PASS: 8 operation scope regression checks')
