"""Isolated AST extraction: no FastAPI, database or app imports required."""
import ast
import re
from pathlib import Path

src = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
tree = ast.parse(src.read_text(encoding='utf-8'))
names = {'canonical', 'mentioned', 'scoped_country_policy', 'explicit_allowed_values', 'detect_value_rule_conflicts'}
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
ns = {'re': re}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(src), 'exec'), ns)
policy = ns['scoped_country_policy']
allowed = ns['explicit_allowed_values']
conflicts = ns['detect_value_rule_conflicts']

shared = ('Change countryCode validation for customer creation and update to accept only IN, US and GB '
          'and reject CA and FR. Do not change response fields, email validation, accountType mapping '
          'or customer deletion behavior.')
assert policy(shared, 'countryCode') is None
assert allowed(shared, 'countryCode') == ['IN', 'US', 'GB'], allowed(shared, 'countryCode')
assert conflicts(shared, ['countryCode']) == [], conflicts(shared, ['countryCode'])

implicit = ('Accept only IN, US and GB for new customers. For existing customers during update, '
            'also accept CA and FR as legacy values. Explicitly report that create and update policies differ.')
expected = policy(implicit, 'countryCode')
assert expected is not None, expected
assert expected['create_allowed'] == ['IN', 'US', 'GB'], expected
assert expected['update_additionally_allowed'] == ['CA', 'FR'], expected

contradiction = 'For countryCode, accept CA and FR but reject CA and FR.'
assert policy(contradiction, 'countryCode') is None
assert conflicts(contradiction, ['countryCode'])[0]['conflicting_values'] == ['CA', 'FR']

assert policy('For customer creation and update accept only IN, US and GB.', 'countryCode') is None
assert policy('For new customers accept only IN, US and GB. For existing customers also accept IN and US.', 'countryCode') is None
print('PASS: 8 country-policy and conflict regression assertions')
