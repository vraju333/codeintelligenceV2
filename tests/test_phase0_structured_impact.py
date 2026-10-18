"""Deterministic live Java impact regression tests (no DB/network)."""
import tempfile
from pathlib import Path

from services.agent.evidence_integrity import assess, guard_answer


def _project(tmp):
    p = Path(tmp) / 'src/main/java/demo'
    p.mkdir(parents=True)
    (p / 'CustomerXmlMapper.java').write_text('''package demo;
public class CustomerXmlMapper {
 public Customer toModel(CustomerXmlRequest source) {
   Customer target = new Customer();
   target.setCountryCode(upper(source.getCountryCode()));
   return target;
 }
}
''')
    (p / 'CustomerService.java').write_text('''package demo;
public class CustomerService {
 public void validate(Customer c) {
   require(c.getCountryCode(), "countryCode");
 }
 public void create(Customer c) {
   validate(c);
 }
}
''')


def test_direct_source_is_not_inferred():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp)
        q = 'Which Java methods directly reference or transform countryCode? Separately list verified call-graph relationships and inferred downstream impact candidates.'
        e = assess(q, [], project_root=tmp)
        assert e['verified_java_impact']['status'] == 'VERIFIED_LIVE_SOURCE'
        answer = guard_answer(q, '### Inferred Downstream Impact Candidates\nCustomerXmlMapper.toModel', e)
        assert '**CustomerXmlMapper.toModel** — `DIRECT_SOURCE`' in answer
        assert '**CustomerService.validate** — `DIRECT_SOURCE`' in answer
        assert '### Inferred Downstream Impact Candidates' not in answer
        assert 'No `VERIFIED_CALL_GRAPH` edges are asserted' in answer
        assert 'CustomerService.create** — `DIRECT_SOURCE`' not in answer


def test_no_live_source_does_not_fabricate():
    e = assess('Which methods directly reference countryCode?', [], project_root='/no/such/path')
    assert e['verified_java_impact']['status'] == 'UNAVAILABLE'
    answer = guard_answer('Which methods directly reference countryCode?', 'No data', e)
    assert 'live working-tree evidence was unavailable' in answer


def test_git_absence_is_not_proven_without_git_tool():
    q = 'Show current countryCode implementation and historical versions or baselines from Git history.'
    e = assess(q, [])
    answer = guard_answer(q, 'No historical versions or baselines were found in Git history.\nCurrent code is available.', e)
    assert 'No historical versions or baselines were found in Git history' not in answer
    assert 'Git history: not verified' in answer
    assert 'Captured baseline history: not verified' in answer


def test_actual_history_tool_is_recorded():
    e = assess('Show historical versions', [{'name': 'baseline_history', 'content': '{}'}])
    assert 'baseline_history' in e['historical_tools_executed']
