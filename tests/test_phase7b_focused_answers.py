import ast
import re
from pathlib import Path

router = Path(__file__).resolve().parents[1] / 'routers' / 'knowledge_agent_router.py'
module = ast.parse(router.read_text(encoding='utf-8'))
functions = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in ('_focus_kind', '_focused_evidence', '_reconcile_ast_graph')]
ns = {'re': re}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(router), 'exec'), ns)

analysis = {'java_ast_evidence': {'status': 'AST_INSPECTED', 'calls': [
    {'method': 'CustomerController.updateAccountType', 'target': 'CustomerService.updateAccountType', 'status': 'SYMBOL_RESOLVED'},
    {'method': 'CustomerController.create', 'target': 'CustomerService.create', 'status': 'SYMBOL_RESOLVED'}],
    'conditions': [{'method': 'CustomerService.validate', 'expression': 'countryCode != null'}]},
    'field_flow_evidence': [{'kind': 'FIELD_GETTER_CALL', 'method': 'CustomerService.validate'},
                            {'kind': 'FIELD_SETTER_CALL', 'method': 'CustomerService.updateAccountType'}],
    'impact_graph': {'edges': [{'from': 'a', 'to': 'b', 'status': 'SOURCE_REFERENCE_ONLY'}]}}
result = {'attribute_verified_paths': [{'endpoint': 'POST /api/customers'}],
          'unverified_attribute_paths': [{'endpoint': 'PATCH /api/customers/{customerId}/account-type'}]}

def test_each_question_gets_its_own_focus():
    cases = [('Show all conditional validation branches for countryCode', 'CONDITIONS'),
             ('Which methods actually modify accountType versus only read it?', 'READ_WRITE'),
             ('Show the Java AST call path for accountType from PATCH endpoint to service.', 'AST_CALLS'),
             ('Show verified and unverified impact relationships for countryCode.', 'RELATIONSHIPS'),
             ('Does changing countryCode impact PATCH /api/customers/{customerId}/account-type?', 'ENDPOINT_IMPACT')]
    for query, kind in cases:
        assert ns['_focus_kind'](query) == kind
        assert ns['_focused_evidence'](analysis, result, query, 'countryCode')['kind'] == kind

def test_call_path_is_route_filtered_and_graph_is_not_promoted():
    focused = ns['_focused_evidence'](analysis, result, 'Show the Java AST call path for accountType from PATCH endpoint to service.', 'accountType')
    assert len(focused['calls']) == 1
    graph = ns['_focused_evidence'](analysis, result, 'Show verified and unverified impact relationships for countryCode.', 'countryCode')
    assert not graph['verified'] and len(graph['unverified']) == 1

def test_patch_not_claimed_verified():
    focused = ns['_focused_evidence'](analysis, result, 'Does changing countryCode impact PATCH /api/customers/{customerId}/account-type?', 'countryCode')
    assert focused['conclusion'] == 'NOT_VERIFIED'


def test_ast_graph_reconciliation_is_edge_type_safe():
    graph = {'edges': [
        {'from': 'method:CustomerController.create', 'to': 'method:CustomerService.create', 'status': 'LEXICAL_NOT_TYPE_RESOLVED'},
        {'from': 'attribute:countryCode', 'to': 'method:CustomerService.create', 'status': 'SOURCE_REFERENCE_ONLY'},
        {'from': 'api:POST /api/customers', 'to': 'method:CustomerController.create', 'status': 'ENDPOINT_SOURCE_CANDIDATE'}]}
    ast = {'calls': [{'method': 'CustomerController.create', 'target': 'com.example.CustomerService.create', 'status': 'SYMBOL_RESOLVED'}]}
    reconciled = ns['_reconcile_ast_graph'](graph, ast)
    assert reconciled['ast_verified_call_edges'] == 1
    assert reconciled['edges'][0]['status'] == 'AST_CALL_VERIFIED'
    assert all(e['status'] != 'AST_CALL_VERIFIED' for e in reconciled['edges'][1:])
    assert graph['edges'][0]['status'] == 'LEXICAL_NOT_TYPE_RESOLVED'
