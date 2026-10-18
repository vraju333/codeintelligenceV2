"""Guardrails for unified current-change evidence, without DB or network."""
import json
from services.agent.evidence_integrity import assess, guard_answer


def test_unified_evidence_does_not_claim_unavailable_live_source():
    report = {
        'status': 'FOUND',
        'git_evidence': {'git_root': '/tmp/example', 'constant_value_changes': [{'symbol': 'COUNTRY', 'before': '{2,3}', 'after': '{2}'}]},
        'attribute_impacts': [{
            'attribute': 'countryCode', 'evidence_classification': 'SOURCE_CONSTANT_AND_GETTER_USAGE',
            'related_jira_requirements': [{'jira_id': 'CAS-2'}],
            'business_operations': [{'http_method': 'POST', 'endpoint': '/api/customers', 'evidence': 'CONTROLLER_ANNOTATION_AND_SERVICE_METHOD_CALL'}],
        }],
    }
    query = 'Analyze my uncommitted Java changes against HEAD with direct impacts and related JIRAs'
    evidence = assess(query, [{'name': 'current_change_traceability', 'content': json.dumps(report)}], project_root='/nonexistent')
    answer = guard_answer(query, 'COUNTRY changed. POST /api/customers. CAS-2 is a requirement match.', evidence)
    assert 'live working-tree evidence was unavailable' not in answer
    assert 'model-generated classifications are not verified' not in answer
    assert 'Impact classification: methods with exact source' not in answer
    assert 'HTTP route evidence: controller annotations' in answer
    assert 'requirement-level matches only' in answer


def test_no_unified_evidence_retains_conservative_warning():
    query = 'Which methods directly reference countryCode?'
    evidence = assess(query, [], project_root='/nonexistent')
    answer = guard_answer(query, 'Unknown', evidence)
    assert 'live working-tree evidence was unavailable' in answer


def test_unverified_unified_tool_is_not_promoted():
    query = 'Analyze direct impact of my uncommitted changes'
    report = {'status': 'FOUND', 'git_evidence': {}, 'attribute_impacts': []}
    evidence = assess(query, [{'name': 'current_change_traceability', 'content': json.dumps(report)}], project_root='/nonexistent')
    assert evidence['verified_current_change_traceability'] is None
