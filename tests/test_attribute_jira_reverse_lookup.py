from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.jira.attribute_jira_lookup_service import _mentions, lookup


def test_identifier_aliases_and_boundaries():
    assert _mentions('Validate countryCode exactly two letters', 'countryCode')
    assert _mentions('Validate country_code exactly two letters', 'countryCode')
    assert _mentions('XML CountryCode value', 'countryCode')
    assert _mentions('Country Code validation', 'countryCode')
    assert not _mentions('discount code', 'countryCode')
    assert not _mentions('othercountryCodeValue', 'countryCode')


def test_reverse_lookup_requirement_match_is_not_implementation_link():
    db = Mock()
    jira = SimpleNamespace(jira_id='CAS-2', title='Country Code Validation',
                           requirement='countryCode must be exactly 2 letters',
                           project_path='jira://CAS')
    db.query.return_value.filter.return_value.all.return_value = [
        SimpleNamespace(source_type='JIRA', project_path='jira://CAS')]
    db.query.return_value.order_by.return_value.all.return_value = [jira]
    with patch('services.jira.attribute_jira_lookup_service.settings') as settings:
        settings.JAVA_PROJECT_PATH = r'D:\AIlearning\customer-account-service'
        result = lookup(db, 'countryCode')
    assert result['match_count'] == 1
    assert result['matches'][0]['jira_id'] == 'CAS-2'
    assert not result['matches'][0]['implementation_link_verified']
    assert not result['matches'][0]['baseline_link_verified']
