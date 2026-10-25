"""Standalone checks for Phase 7 source-backed attribute path classification."""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'services/agent/ask_evidence_service.py'

def load_summary():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'_summary', '_endpoint_attribute_conflict'}]
    code = compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), 'exec')
    namespace = {'CONSTANTS': {'countryCode': 'COUNTRY'}, '_matching_change': lambda s,a: s.get('constant') == 'COUNTRY' and s.get('kind') in ('PATTERN_CHANGED','DEPENDS_ON_CHANGED_PATTERN'), 'enrich_commits': lambda p,c: c, 'format_commit_history': lambda c: []}
    exec(code, namespace)
    return namespace['_summary']

class EvidenceClassificationTests(unittest.TestCase):
    def test_unrelated_controller_is_not_attribute_verified(self):
        report = {'semantic_changes': [
            {'kind':'PATTERN_CHANGED','constant':'COUNTRY','commit':'abc'},
            {'kind':'DEPENDS_ON_CHANGED_PATTERN','constant':'COUNTRY','commit':'abc','method':'validate'}],
            'git': {'commits': []}, 'project_path': 'sample', 'jira': 'CAS-2',
            'source_verified_paths': [
                {'commit':'abc','changed_method':'validate','endpoint':'POST /api/customers'},
                {'commit':'abc','changed_method':'updateAccountType','endpoint':'PATCH /api/customers/{id}/account-type'}],
            'qa': {'test_cases': []}}
        result = load_summary()(report, attribute='countryCode')
        self.assertEqual(result['source_verified_endpoints'], ['POST /api/customers'])
        self.assertEqual(len(result['unverified_attribute_paths']), 1)
        self.assertIn('PATCH /api/customers', result['answer'])
        self.assertNotIn('PATCH /api/customers', result['answer'].split('Attribute-verified API paths:')[1].split('\n')[0])

if __name__ == '__main__': unittest.main()
