"""Focused regressions for evidence precision and project isolation."""
from types import SimpleNamespace
from services.jira.jira_code_evidence_resolver import resolve_jira_code_candidates


class Query:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class DB:
    def __init__(self, jiras, mappings): self.jiras, self.mappings = jiras, mappings
    def query(self, model):
        from db_models import JiraKnowledge
        return Query(self.jiras if model is JiraKnowledge else self.mappings)


def test_jira_candidate_matches_business_attribute_not_generic_words(tmp_path):
    (tmp_path / 'CustomerService.java').write_text('''
public class CustomerService {
 public void validate() {
  require(c.getCountryCode(), "countryCode");
  throw new IllegalArgumentException("Invalid email");
  require(c.getAccountType(), "accountType");
  if (!COUNTRY.matcher(c.getCountryCode()).matches()) { }
 }
}
''')
    db = DB([SimpleNamespace(jira_id='CAS-2', project_path=str(tmp_path),
             title='Update countryCode validation', requirement='Accept exactly two letters; reject invalid values')],
            [SimpleNamespace(project_path=str(tmp_path), target_attribute='countryCode'),
             SimpleNamespace(project_path=str(tmp_path), target_attribute='accountType')])
    result = resolve_jira_code_candidates(db, str(tmp_path), jira_ids=['CAS-2'])
    statements = [x['statement'] for x in result['candidates'][0]['source_evidence']]
    assert any('getCountryCode' in x for x in statements)
    assert not any('Invalid email' in x or 'getAccountType' in x for x in statements)


def test_jira_candidate_does_not_use_other_project_mapping(tmp_path):
    (tmp_path / 'CustomerService.java').write_text('public class CustomerService { public void validate() { require(c.getCountryCode(), "countryCode"); } }')
    db = DB([SimpleNamespace(jira_id='CAS-2', project_path=str(tmp_path),
             title='Change validation', requirement='Reject invalid values')],
            [SimpleNamespace(project_path='/other/project', target_attribute='countryCode')])
    result = resolve_jira_code_candidates(db, str(tmp_path), jira_ids=['CAS-2'])
    assert result['candidates'] == []
