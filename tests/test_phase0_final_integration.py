"""Phase 0 final evidence behavior (no live credentials required)."""
import importlib.util
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'services' / 'agent' / 'evidence_integrity.py'
spec = importlib.util.spec_from_file_location('evidence_integrity_final', P)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def test_no_internal_omission_markers():
    evidence = module.assess('Which tests cover CAS-1?', [])
    answer = module.guard_answer('Which tests cover CAS-1?', 'These scenarios confirm test coverage.\nConfirmed test scenarios: POST.', evidence)
    assert 'UNVERIFIED COVERAGE CLAIM OMITTED' not in answer
    assert 'No explicit Jira-to-test-baseline' in answer

def test_direct_source_rule_present():
    evidence = module.assess('Which Java methods directly transform countryCode?', [])
    answer = module.guard_answer('Which Java methods directly transform countryCode?', 'Candidate methods', evidence)
    assert 'DIRECT_SOURCE' in answer
    assert 'VERIFIED_CALL_GRAPH' in answer

def test_live_source_fingerprint_is_exposed():
    rows = [{'name': 'java_source_search', 'content': '{"exact_matches":[{"evidence":"CURRENT_JAVA_SOURCE","file_path":"/repo/C.java","sha256":"1234"}]}'}]
    evidence = module.assess('Show current source', rows)
    answer = module.guard_answer('Show current source', 'Code', evidence)
    assert 'SHA-256 1234' in answer
