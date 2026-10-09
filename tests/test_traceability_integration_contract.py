"""Offline regression checks for the integration patch (no database required)."""
import ast
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'services/knowledge/requirement_traceability_service.py'

def test_traceability_python_compiles():
    ast.parse(SOURCE.read_text(encoding='utf-8'))

def test_method_call_pattern_compiles_and_matches():
    pattern = re.compile(r'\b([A-Za-z_]\w*)\s*\(')
    assert 'toEntity' in pattern.findall('mapper.toEntity(customer);')

def test_jira_key_boundary():
    key = re.compile(r'(?<![A-Z0-9])CAS\-1(?![A-Z0-9])', re.I)
    assert key.search('// implements CAS-1')
    assert not key.search('// CAS-10')

def test_no_inferred_implements_relationship():
    source = SOURCE.read_text(encoding='utf-8')
    assert 'MERGE (m)-[r:REFERENCES_JIRA]->(j)' in source
    assert 'MERGE (m)-[r:IMPLEMENTS]' not in source

def test_project_scope_is_used_for_scenarios_and_mappings():
    source = SOURCE.read_text(encoding='utf-8')
    assert '_canon(sc.project_path) == _canon(project_path)' in source
    assert '_canon(row.project_path) == _canon(project_path)' in source
