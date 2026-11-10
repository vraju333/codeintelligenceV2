"""Verify operation-scoped recommendation generation without database imports."""
import ast
from pathlib import Path

source = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
# Execute exactly the recommendation loop from the endpoint to verify emitted cases.
loops = [n for n in ast.walk(tree) if isinstance(n, ast.For)
         and isinstance(n.iter, ast.Call)
         and isinstance(n.iter.func, ast.Attribute)
         and n.iter.func.attr == 'get'
         and n.iter.args and isinstance(n.iter.args[0], ast.Constant)
         and n.iter.args[0].value == 'operation_scoped_policies']
assert len(loops) == 1, f'Expected one policy loop; found {len(loops)}'
block = ast.fix_missing_locations(ast.Module(body=[loops[0]], type_ignores=[]))
ns = {
    'intent': {'operation_scoped_policies': [{
        'attribute': 'countryCode',
        'create_allowed': ['IN', 'US', 'GB'],
        'update_additionally_allowed': ['CA', 'FR']}]},
    'regression_recommendations': [],
    'recommendation_class': 'REQUIREMENT_DERIVED_NOT_EXECUTED',
}
exec(compile(block, str(source), 'exec'), ns)
items = ns['regression_recommendations']
assert len(items) == 10, items
cases = {(x['operation'].split()[0], x['input_value'], x['expected']) for x in items}
assert len(cases) == 10
for val in ('IN', 'US', 'GB'):
    assert ('POST', val, 'ACCEPT') in cases
    assert ('PUT', val, 'ACCEPT') in cases
for val in ('CA', 'FR'):
    assert ('POST', val, 'REJECT') in cases
    assert ('PUT', val, 'ACCEPT_FOR_EXISTING_CUSTOMER') in cases
print('PASS: 10 scoped-policy recommendations across POST and PUT')
