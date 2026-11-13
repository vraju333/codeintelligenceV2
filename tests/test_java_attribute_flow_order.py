"""Run from backend root: pytest tests/test_java_attribute_flow_order.py"""
import importlib
import sys
import types
import re
import pytest

@pytest.fixture(autouse=True)
def stub_graph_dependency(monkeypatch):
    # Backend supplies this existing module; isolate the changed service in tests.
    name="services.agent.phase2_flow_service"
    if name not in sys.modules:
        stub=types.ModuleType(name)
        stub._project=lambda root:([],[])
        stub._calls=lambda n, byname:[]
        stub.TARGET=re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
        monkeypatch.setitem(sys.modules,name,stub)

from pathlib import Path
from unittest.mock import patch


def test_nested_calls_are_reported_inner_first(tmp_path):
    mod = importlib.import_module('services.agent.java_attribute_flow_service')
    def node(cls, name, source, line=1):
        return {'id': cls+'.'+name, 'cls': cls, 'name': name, 'file':cls+'.java',
                'line':line, 'source':source, 'signature':name+'()'}
    nodes = [
        node('CustomerService','create', 'public Customer create() {\n return jpaMapper.toModel(repository.save(jpaMapper.toEntity(customer)));\n}',10),
        node('CustomerJpaMapper','toEntity','public Entity toEntity(Customer source) {\n target.setAccountType(source.getAccountType());\n}',20),
        node('CustomerJpaMapper','toModel','public Customer toModel(Entity source) {\n target.setAccountType(source.getAccountType());\n}',30),
    ]
    def calls(n, byname):
        if n['name']!='create': return []
        return [{'line':11,'expression':expr,'status':'RECEIVER_TYPE_INFERRED' if 'jpaMapper' in expr else 'FRAMEWORK_MEDIATED_CANDIDATE',
                 'targets':[target] if target else [],'name':name}
                for expr,name,target in [('jpaMapper.toModel(', 'toModel','CustomerJpaMapper.toModel'),
                                         ('repository.save(', 'save',None),
                                         ('jpaMapper.toEntity(', 'toEntity','CustomerJpaMapper.toEntity')]]
    with patch.object(mod,'_project',return_value=(nodes,[])),patch.object(mod,'_calls',side_effect=calls):
        result=mod.answer('Trace accountType through CustomerService.create() and repository',str(tmp_path))
    titles=[x['title'] for x in result['ordered_flow_evidence']]
    assert titles.index('Invoke domain-to-entity mapping') < titles.index('Invoke repository save (not a verified database write)')
    assert titles.index('Invoke repository save (not a verified database write)') < titles.index('Invoke entity-to-domain mapping')
    assert result['cross_method_dataflow_verified'] is False


def test_explicit_method_not_substituted(tmp_path):
    mod=importlib.import_module('services.agent.java_attribute_flow_service')
    with patch.object(mod,'_project',return_value=([],[])):
        result=mod.answer('Trace accountType through CustomerController.create()',str(tmp_path))
    assert result['status']=='NOT_FOUND'


def test_mapper_statement_consolidated(tmp_path):
    mod=importlib.import_module('services.agent.java_attribute_flow_service')
    node={'id':'CustomerXmlMapper.toModel','cls':'CustomerXmlMapper','name':'toModel',
          'file':'CustomerXmlMapper.java','line':10,
          'source':'public Customer toModel(Request source) {\n target.setAccountType(upper(source.getAccountType()));\n}',
          'signature':'toModel()'}
    edges=[{'line':11,'expression':expr,'status':'RECEIVER_TYPE_INFERRED','targets':[], 'name':name}
           for expr,name in [('source.getAccountType(','getAccountType'),('target.setAccountType(','setAccountType')]]
    with patch.object(mod,'_project',return_value=([node],[])),patch.object(mod,'_calls',return_value=edges):
        result=mod.answer('Trace accountType in CustomerXmlMapper.toModel()',str(tmp_path))
    statements=[x['statement'] for x in result['ordered_flow_evidence']]
    assert statements==['target.setAccountType(upper(source.getAccountType()));']


def test_multiline_setter_is_single_step(tmp_path):
    mod=importlib.import_module('services.agent.java_attribute_flow_service')
    node={'id':'CustomerService.updateAccountType','cls':'CustomerService','name':'updateAccountType',
          'file':'CustomerService.java','line':30,
          'source':'public Customer updateAccountType(String accountType) {\n customer.setAccountType(\n accountType == null\n ? null\n : accountType.trim().toUpperCase());\n}',
          'signature':'updateAccountType()'}
    edges=[{'line':31,'expression':'customer.setAccountType(','status':'RECEIVER_TYPE_INFERRED','targets':[], 'name':'setAccountType'}]
    with patch.object(mod,'_project',return_value=([node],[])),patch.object(mod,'_calls',return_value=edges):
        result=mod.answer('Trace accountType in CustomerService.updateAccountType()',str(tmp_path))
    statements=[x['statement'] for x in result['ordered_flow_evidence']]
    assert len(statements)==1
    assert 'trim().toUpperCase()' in statements[0]


def test_comparison_has_both_explicit_methods_and_validation(tmp_path):
    mod=importlib.import_module('services.agent.java_attribute_flow_service')
    def node(cls,name,source,line):
        return dict(id=cls+'.'+name,cls=cls,name=name,source=source,line=line,file=cls+'.java',signature=name+'()')
    nodes=[
        node('CustomerXmlMapper','toModel','public Customer toModel(Request source) {\n target.setAccountType(upper(source.getAccountType()));\n}',10),
        node('CustomerService','updateAccountType','public void updateAccountType(String accountType) {\n customer.setAccountType(accountType == null ? null : accountType.trim().toUpperCase());\n}',20),
        node('CustomerService','validate','private void validate(Customer c) {\n require(c.getAccountType(), "accountType");\n if (!ACCOUNT_TYPES.contains(c.getAccountType())) throw new IllegalArgumentException();\n}',30)]
    with patch.object(mod,'_project',return_value=(nodes,[])):
        result=mod.answer('Compare how accountType is transformed in CustomerXmlMapper.toModel() and CustomerService.updateAccountType(). Show input, output, and validation rules.',str(tmp_path))
    assert result['mode']=='attribute_comparison'
    assert 'upper(source.getAccountType())' in result['answer']
    assert 'trim().toUpperCase()' in result['answer']
    assert 'require(c.getAccountType()' in result['answer']
    assert result['cross_method_dataflow_verified'] is False


def test_branch_report_includes_repository_guard_and_id_fallback(tmp_path):
    mod=importlib.import_module('services.agent.java_attribute_flow_service')
    nodes=[dict(id='CustomerService.replace',cls='CustomerService',name='replace',file='CustomerService.java',line=40,
        source='public Customer replace(String customerId) {\n if (!repository.existsById(customerId))\n throw new CustomerNotFoundException(customerId);\n if (customer.getCustomerId() == null || customer.getCustomerId().isBlank())\n customer.setCustomerId(customerId);\n if (!customerId.equals(customer.getCustomerId()))\n throw new IllegalArgumentException();\n validate(customer);\n}',signature='replace()')]
    with patch.object(mod,'_project',return_value=(nodes,[])):
        result=mod.answer('Analyze CustomerService.replace() for customerId. Show conditions, fallback, and alternative paths.',str(tmp_path))
    assert result['mode']=='attribute_branches'
    assert 'existsById' in result['answer']
    assert 'setCustomerId' in result['answer']
    assert 'path-sensitive CFG' in result['answer']
