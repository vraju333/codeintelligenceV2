from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from services.agent.ask_evidence_service import detect_attribute, is_change_question, _summary, attribute_change_history
import sys
from unittest.mock import MagicMock as _Mock
sys.modules.setdefault("services.agent.knowledge_agent_service", _Mock())
from routers.knowledge_agent_router import format_qa_evidence


def test_attribute_spelling_and_intent():
    for text in ('countryCode changes', 'country code validation', 'country_code changed'):
        assert detect_attribute(text) == 'countryCode'
    assert detect_attribute('customer email related JIRAs') == 'customerEmail'
    assert is_change_question('Which JIRA changed country code validation?')
    assert not is_change_question('country code jiras please')


def sample_report(jira, constant):
    return {'jira': jira, 'semantic_changes': [{'kind': 'PATTERN_CHANGED', 'constant': constant, 'before': 'old', 'after': 'new', 'commit': 'sha'},
                                              {'kind': 'DEPENDS_ON_CHANGED_PATTERN', 'constant': constant, 'method': 'validate', 'commit': 'sha'}],
            'git': {'commits': [{'sha': 'sha'}]},
            'source_verified_paths': [{'commit': 'sha', 'endpoint': 'POST /api/customers'}],
            'qa': {'test_cases': [], 'tests_with_recorded_execution': 0}}


def test_semantic_attribute_filter_does_not_conflate_shared_validate():
    assert _summary(sample_report('CAS-2', 'COUNTRY'), attribute='countryCode')['matched_change']
    assert not _summary(sample_report('CAS-3', 'EMAIL'), attribute='countryCode')['matched_change']


def test_attribute_history_excludes_requirement_only_mentions():
    with patch('services.agent.ask_evidence_service.lookup', return_value={'matches': [{'jira_id': 'CAS-1'}, {'jira_id': 'CAS-2'}]}), \
         patch('services.agent.ask_evidence_service.build_report', side_effect=[sample_report('CAS-1', 'EMAIL'), sample_report('CAS-2', 'COUNTRY')]):
        result = attribute_change_history(MagicMock(), 'Which Jira changed country code?', 'countryCode')
    assert result['verified_change_jiras'] == ['CAS-2']
    assert result['candidate_jiras'] == ['CAS-1', 'CAS-2']


def test_qa_duplicate_sources_consolidated():
    records = {'count': 2, 'test_cases': [
        {'jira': 'CAS-2', 'test_id': 'CAS2-TC001', 'scenario': 'Valid IN', 'expected': 'Accepted', 'source_kind': 'FOLDER', 'source_filename': 'local.xlsx', 'source_ref': 'local'},
        {'jira': 'CAS-2', 'test_id': 'cas2-tc001', 'scenario': 'Valid IN', 'expected': 'Accepted', 'source_kind': 'JIRA', 'source_filename': 'jira.xlsx', 'source_ref': 'jira:CAS-2:attachment:10000'}]}
    answer = format_qa_evidence(records)
    assert 'Unique QA cases: 1' in answer
    assert answer.count('execution=NOT_VERIFIED') == 1
    assert 'local.xlsx' in answer and 'jira.xlsx' in answer
