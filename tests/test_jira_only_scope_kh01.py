from types import SimpleNamespace
from services.agent.ask_jira_requirement_service import jira_requirement_only_request, jira_requirement_only_answer

QUESTION = ('Show the complete requirement and acceptance criteria for JIRA CAS-2 in customer-account-service. '
            'Identify its status, related attributes, and source evidence. '
            'Do not include unrelated JIRAs, inferred code changes, or mapping documents.')


def test_jira_only_routing_and_exclusions():
    assert jira_requirement_only_request(QUESTION)
    assert not jira_requirement_only_request('Analyze CAS-2 impact on Java methods, mappings and test cases')
    assert not jira_requirement_only_request('Compare CAS-2 requirement with mapping documents')
    assert not jira_requirement_only_request('List imported test cases linked to CAS-2')


def test_jira_only_response(monkeypatch):
    import db_models
    from services.lineage.mapping_intelligence_service import MappingIntelligenceService
    monkeypatch.setattr(MappingIntelligenceService, '_canonical_project_key', staticmethod(lambda x: str(x).lower().replace('\\', '/')))
    row = SimpleNamespace(jira_id='CAS-2', title='Country code', project_path='D:/repo', id=2,
        updated_at=None, requirement='Accept exactly 2 letters.\nAcceptance Criteria:\nAccept IN and US.\nReject USA.\nImpacted Business Attribute:\nCountry Code\nJira metadata: {"status":"To Do","issue_type":"Story"}')
    class Query:
        def filter(self, *args): return self
        def all(self): return [row]
    class DB:
        def query(self, *args): return Query()
    result = jira_requirement_only_answer(DB(), QUESTION, 'D:/repo')
    assert result['status'] == 'FOUND'
    assert result['jira']['status'] == 'To Do'
    assert result['related_attributes'] == ['Country Code']
    assert 'Reject USA' in result['acceptance_criteria']
    assert all(key not in result for key in ('historical_jiras', 'mappings', 'code_impact', 'test_suites'))
    assert jira_requirement_only_answer(DB(), QUESTION, 'D:/other')['status'] == 'NOT_FOUND'
