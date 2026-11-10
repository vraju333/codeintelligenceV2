"""Run: python tests/test_requirement_operation_regressions.py (no DB required)."""
import ast
import re
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
names = {'canonical', 'mentioned', '_retrieval_change_attributes', '_operation_change_scope'}
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
ns = {'re': re, '_endpoint_targets_attribute': lambda endpoint, method, attrs: any(a.casefold() in (endpoint + method).casefold() for a in attrs)}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), ns)
scope = ns['_operation_change_scope']

mixed = ('Add optional customerTier with STANDARD, GOLD and PLATINUM values for customer creation and update. '
         'Persist it and return it in retrieval responses. Existing customers without customerTier must remain supported. '
         'Also change countryCode validation to accept only IN, US and GB and reject CA and FR. '
         'Do not modify email validation, accountType mapping or customer deletion behavior. '
         'Identify API impact, mapping gaps and regression tests.')
validation = 'Change countryCode validation to accept only IN, US and GB and reject CA and FR.'
implicit = ('Accept only IN, US and GB for new customers. For existing customers during update, '
            'also accept CA and FR as legacy values. Explicitly report that create and update policies differ.')
contradiction = 'For countryCode, accept CA and FR but reject CA and FR.'

cases = [
    ('mixed GET', 'GET', mixed, ['customerTier', 'countryCode'], ['customerTier'], False, ['customerTier'], 'PROPOSED_IMPLEMENTATION'),
    ('mixed POST', 'POST', mixed, ['customerTier', 'countryCode'], ['customerTier'], False, ['customerTier', 'countryCode'], 'PROPOSED_IMPLEMENTATION'),
    ('mixed PUT', 'PUT', mixed, ['customerTier', 'countryCode'], ['customerTier'], False, ['customerTier', 'countryCode'], 'PROPOSED_IMPLEMENTATION'),
    ('mixed DELETE', 'DELETE', mixed, ['customerTier', 'countryCode'], ['customerTier'], False, [], 'REGRESSION_ONLY_OR_UNRELATED'),
    ('validation GET', 'GET', validation, ['countryCode'], [], False, [], 'REGRESSION_ONLY_OR_UNRELATED'),
    ('validation POST', 'POST', validation, ['countryCode'], [], False, ['countryCode'], 'PROPOSED_IMPLEMENTATION'),
    ('validation PUT', 'PUT', validation, ['countryCode'], [], False, ['countryCode'], 'PROPOSED_IMPLEMENTATION'),
    ('implicit GET', 'GET', implicit, ['countryCode'], [], False, [], 'REGRESSION_ONLY_OR_UNRELATED'),
    ('conflict GET', 'GET', contradiction, ['countryCode'], [], True, [], 'PROVISIONAL_PENDING_CLARIFICATION'),
    ('conflict POST', 'POST', contradiction, ['countryCode'], [], True, [], 'PROVISIONAL_PENDING_CLARIFICATION'),
    ('conflict PUT', 'PUT', contradiction, ['countryCode'], [], True, [], 'PROVISIONAL_PENDING_CLARIFICATION'),
    ('explicit new field GET', 'GET', 'Add customerTier and return customerTier in retrieval responses.', ['customerTier'], ['customerTier'], False, ['customerTier'], 'PROPOSED_IMPLEMENTATION'),
    ('no return new field GET', 'GET', 'Add customerTier to create and update requests.', ['customerTier'], ['customerTier'], False, [], 'REGRESSION_ONLY_OR_UNRELATED'),
    ('no accidental GET', 'GET', 'Return customerTier in responses. Change countryCode validation.', ['customerTier','countryCode'], ['customerTier'], False, ['customerTier'], 'PROPOSED_IMPLEMENTATION'),
]
for label, verb, req, changed, new, unresolved, attrs, status in cases:
    actual = scope({'http_method': verb, 'endpoint': '/api/customers', 'controller_method': verb.lower()}, changed, req, unresolved, new)
    assert actual == (attrs, status), (label, actual, (attrs, status))
patch = scope({'http_method':'PATCH','endpoint':'/api/customers/{customerId}/account-type','controller_method':'updateAccountType'}, ['customerTier','countryCode'], mixed, False, ['customerTier'])
assert patch == ([], 'INDIRECT_REGRESSION'), patch
print('PASS:', len(cases) + 1, 'operation-scope regression cases')
