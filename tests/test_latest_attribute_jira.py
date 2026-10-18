"""Run with backend dependencies: pytest -q tests/test_latest_attribute_jira.py"""
from unittest.mock import patch
from services.agent.latest_attribute_jira_service import is_latest_jira_question, latest_attribute_jira


def test_latest_query_detected():
    assert is_latest_jira_question('latest jira for country code')
    assert not is_latest_jira_question('country code jiras please')
    assert not is_latest_jira_question('latest code change for country code')


def test_latest_uses_jira_created_not_git():
    matches = {'matches': [{'jira_id': 'CAS-1', 'summary': 'Ingest'},
                           {'jira_id': 'CAS-2', 'summary': 'Validate'},
                           {'jira_id': 'CAS-5', 'summary': 'Enhance'}]}
    class Live:
        def get_issue(self, key):
            return {'summary': key, 'created_at': {'CAS-1': '2026-10-01T10:00:00+05:30',
                                                    'CAS-2': '2026-10-17T10:00:00+05:30',
                                                    'CAS-5': '2026-10-18T10:00:00+05:30'}[key]}
    with patch('services.agent.latest_attribute_jira_service.lookup', return_value=matches):
        result = latest_attribute_jira(None, 'latest jira for country code', live_client=Live())
    assert result['latest_jira']['jira'] == 'CAS-5'
    assert result['recency_verified'] is True


def test_missing_created_never_falsely_claims_latest():
    matches = {'matches': [{'jira_id': 'CAS-2', 'summary': 'Validate'},
                           {'jira_id': 'CAS-5', 'summary': 'Enhance'}]}
    class Live:
        def get_issue(self, key):
            return {'created_at': '2026-10-17T10:00:00+05:30'} if key == 'CAS-2' else {}
    with patch('services.agent.latest_attribute_jira_service.lookup', return_value=matches):
        result = latest_attribute_jira(None, 'latest jira for country code', live_client=Live())
    assert result['status'] == 'RECENCY_NOT_VERIFIED'
    assert result['recency_verified'] is False
