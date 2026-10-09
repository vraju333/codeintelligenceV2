"""Offline regression tests for evidence boundaries and scenario matching."""
import ast
from pathlib import Path
from types import SimpleNamespace
from collections import defaultdict
import re
import json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'services/knowledge/requirement_traceability_service.py'
GRAPH = ROOT / 'services/cross_project/engineering_knowledge_graph_service.py'
VERIFY = ROOT / 'services/knowledge/knowledge_hub_verification_service.py'


def helpers():
    tree = ast.parse(SOURCE.read_text())
    wanted = {'_endpoint_key', '_declared_classes', '_scenario_candidates'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    env = {'re': re, 'json': json, 'defaultdict': defaultdict}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
    return env


def test_syntax_all_changed_files():
    for p in (SOURCE, GRAPH, VERIFY):
        ast.parse(p.read_text())


def test_endpoint_normalization_and_class_parsing():
    env = helpers()
    assert env['_endpoint_key']('post', '/api/customers/{id}/') == ('POST', '/api/customers/{}/')[:1] + ('/api/customers/{}',)
    assert env['_declared_classes']('["com.example.CustomerService", "CustomerController"]') == {'customerservice', 'customercontroller'}


def test_scenario_call_path_and_baseline_not_verified():
    env = helpers()
    scenario = SimpleNamespace(id=3, scenario_code='TEST-3', scenario_name='XML intake',
                               http_method='POST', endpoint='/api/customers', involved_classes=None)
    endpoint_methods = {('POST', '/customers'): {'CustomerController.create'}}
    edges = [('CustomerController.create', 'CustomerService.create'),
             ('CustomerService.create', 'CustomerService.validate')]
    found = env['_scenario_candidates']([scenario], endpoint_methods,
                                        {'CustomerService.validate'}, edges,
                                        {'CustomerService.validate': 'CustomerService'})
    assert len(found) == 1
    assert found[0]['classification'] == 'INFERRED_SCENARIO_CANDIDATE'
    assert found[0]['matched_methods'] == ['CustomerService.validate']
    assert 'POSSIBLY_COVERS_JIRA' in SOURCE.read_text()
    assert "HISTORICAL_BASELINE_NOT_JIRA_VERIFIED" in SOURCE.read_text()


def test_project_scoped_graph_verification():
    src = VERIFY.read_text()
    assert 'mapping_attribute_links' in src
    assert 'inferred_jira_scenario_links' in src
    assert '{project:$project}' in src


def test_receiver_type_resolution_is_conservative():
    src = GRAPH.read_text()
    assert 'typed_method_index.get((field_types[receiver], called_name))' in src
    assert 'if len(targets) == 1:' in src
