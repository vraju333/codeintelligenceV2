"""No network or LLM required: regression checks for evidence-safe ASK routes."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services.agent.ask_accuracy_guard import answer


class Query:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class DB:
    def __init__(self, rows): self.rows = rows
    def query(self, model): return Query(self.rows)


def test_jira_attribute_filters_and_does_not_claim_link():
    rows = [SimpleNamespace(project_path='D:/demo', jira_id='CAS-1', title='customerId validation', requirement='Use customerId'),
            SimpleNamespace(project_path='D:/demo', jira_id='CAS-2', title='country code', requirement='countryCode'),
            SimpleNamespace(project_path='D:/other', jira_id='CAS-3', title='customerId', requirement='customerId')]
    result = answer(DB(rows), 'Find JIRA issues linked to customerId in the currently selected project.', 'D:/demo')
    assert result['candidate_jiras'] == ['CAS-1']
    assert result['verified_jira_links'] == []
    assert 'NOT explicit' in result['answer']


def test_scenario_routes_without_fabricating_test_coverage():
    rows = [SimpleNamespace(project_path='D:/demo', scenario_code='CUS-UPD', scenario_name='Customer update',
                            description='replace customer', endpoint='/api/customers/{customerId}',
                            http_method='PUT', jira_id='CAS-1')]
    result = answer(DB(rows), 'Identify customer-update scenarios that lack linked test evidence.', 'D:/demo')
    assert result['scenarios'][0]['jira_id'] == 'CAS-1'
    assert result['coverage_verified'] is False
    assert 'cannot be determined' in result['answer']


def test_callers_never_promoted_to_compiler_verified():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        (base/'CustomerService.java').write_text('class CustomerService {\n void replace() {}\n }')
        (base/'CustomerController.java').write_text('class CustomerController {\n CustomerService service;\n void update() { service.replace(); }\n }')
        result = answer(DB([]), 'Identify callers of CustomerService.replace(). Distinguish inferred from verified.', folder)
        assert result['verified_callers'] == []
        assert result['compiler_verification'] == 'NOT_PERFORMED'
        assert 'NOT COMPILER VERIFIED' in result['answer']


def test_unrelated_question_not_intercepted():
    assert answer(DB([]), 'Explain Java virtual threads', 'D:/demo') is None


def test_xml_path_mismatch_returns_real_source_line():
    with tempfile.TemporaryDirectory() as folder:
        (Path(folder)/'CustomerService.java').write_text('class CustomerService {\n if (!customerId.equals(customer.getCustomerId())) throw new IllegalArgumentException("Mismatch");\n}')
        result = answer(DB([]), 'What happens if the path customerId and XML CustomerId do not match?', folder)
        assert 'IllegalArgumentException' in result['answer']
        assert result['source'] == 'DETERMINISTIC_JAVA_BUSINESS_RULE'
