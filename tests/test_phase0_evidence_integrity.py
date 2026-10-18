"""Phase 0 regression tests: pure-Python, no DB/LLM/Neo4j required."""
import importlib.util
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'services' / 'agent' / 'evidence_integrity.py'
spec = importlib.util.spec_from_file_location('evidence_integrity_under_test', P)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_discovered_scenarios_are_not_confirmed_coverage():
    rows = [{'name': 'scenario_impact', 'content': '{"affected_scenarios":[{"scenario_code":"CREATE_DATA_API_CUSTOMERS","jira_ids":["CAS-1"]}]}'}]
    evidence = module.assess('Which tests cover CAS-1?', rows)
    assert evidence['jira_coverage']['CAS-1'] == 'NOT_CONFIRMED_FROM_RETURNED_EVIDENCE'
    answer = module.guard_answer('Which tests cover CAS-1?', 'These scenarios confirm test coverage.', evidence)
    assert 'UNVERIFIED COVERAGE CLAIM OMITTED' not in answer
    assert 'No explicit Jira-to-test-baseline' in answer


def test_explicit_baseline_link_is_confirmed():
    rows = [{'name': 'baseline_history', 'content': '{"test_baselines":[{"test_baseline_id":42,"jira_ids":["CAS-1"]}]}'}]
    evidence = module.assess('Which tests cover CAS-1?', rows)
    assert evidence['jira_coverage']['CAS-1'] == 'CONFIRMED_EXPLICIT_LINK'


def test_indexed_source_does_not_prove_current():
    evidence = module.assess('Show current CustomerXmlRequest.countryCode', [{'name':'java_source_search','content':'{"exact_matches":[],"phase8_pgvector_discovery":{"hits":[{"source":"old"}]}}'}])
    assert not evidence['live_source_files']
    assert 'not verified' in module.guard_answer('Show current code', 'Current source is X.', evidence)


def test_live_source_requires_fingerprint():
    rows = [{'name':'java_source_search','content':'{"exact_matches":[{"evidence":"CURRENT_JAVA_SOURCE","file_path":"/repo/CustomerXmlRequest.java","sha256":"abc"}]}'}]
    assert len(module.assess('current source', rows)['live_source_files']) == 1


def test_unfounded_numeric_confidence_is_removed():
    evidence = module.assess('trace countryCode', [])
    answer = module.guard_answer('trace countryCode', '**Overall Confidence**: High (95%)', evidence)
    assert '95%' not in answer
