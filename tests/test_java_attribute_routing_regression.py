"""Prevent ASK from silently falling through to generic LLM for conditional Java questions."""
import ast
from pathlib import Path
from unittest.mock import patch
import importlib

QUERY = ('Analyze CustomerService.replace() for customerId. Show every condition '
         'that can change or reject the ID, including the repository existence check, '
         'missing-ID fallback, path-ID comparison, and validation. '
         'Include source lines and identify alternative execution paths.')


def test_router_dispatches_java_analyzer_before_generic_fallback():
    router = Path(__file__).resolve().parents[1] / 'routers' / 'knowledge_agent_router.py'
    source = router.read_text(encoding='utf-8')
    start = source.index('def ask_knowledge_agent(')
    body = source[start:source.index('@router.post("/graph")', start)]
    assert body.index('from services.agent.java_attribute_flow_service import answer') < body.index('result = _ask_knowledge_agent_impl(payload, db)')
    assert body.index('from services.agent.java_attribute_flow_service import answer') < body.index('from services.agent.phase2_flow_service import answer')


def test_real_conditional_query_matches_intent():
    service = importlib.import_module('services.agent.java_attribute_flow_service')
    assert service.intent(QUERY)


def test_missing_id_is_fallback_not_rejection(tmp_path):
    service = importlib.import_module('services.agent.java_attribute_flow_service')
    node = dict(id='CustomerService.replace',cls='CustomerService',name='replace',file='CustomerService.java',line=75,
                signature='replace()',source='''public Customer replace(String customerId, CustomerXmlRequest request) {
 if (!repository.existsById(customerId))
 throw new CustomerNotFoundException(customerId);
 Customer customer = xmlMapper.toModel(request);
 if (customer.getCustomerId() == null ||
 customer.getCustomerId().isBlank())
 customer.setCustomerId(customerId);
 if (!customerId.equals(customer.getCustomerId()))
 throw new IllegalArgumentException("Path customerId must match XML CustomerId");
 validate(customer);
 return jpaMapper.toModel(repository.save(jpaMapper.toEntity(customer)));
}''')
    validation = dict(id='CustomerService.validate',cls='CustomerService',name='validate',file='CustomerService.java',line=120,
                      signature='validate()',source='private void validate(Customer c) {\n require(c.getCustomerId(), "customerId");\n}')
    with patch.object(service,'_project',return_value=([node,validation],[])):
        result=service.answer(QUERY,str(tmp_path))
    assert result['mode']=='attribute_branches'
    assert result['source']=='JAVA_ATTRIBUTE_FLOW_ANALYSIS'
    assert 'missing/blank mapped ID; assign path customerId and continue' in result['answer']
    assert 'EXCEPTION_EXIT' in result['answer']
    assert 'VALIDATION_CHECK' in result['answer']
    assert 'Java source-backed conditional-path analysis' in result['answer']
    assert result['path_sensitive_cfg_verified'] is False
