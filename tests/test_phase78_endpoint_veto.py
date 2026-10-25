import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'services/agent/ask_evidence_service.py'

class EndpointClassificationTests(unittest.TestCase):
    def test_field_specific_patch_mismatch(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        names = {'_endpoint_attribute_conflict', '_summary'}
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = {'CONSTANTS': {'countryCode': 'COUNTRY'},
              '_matching_change': lambda s,a: s.get('constant') == 'COUNTRY' and s.get('kind') in ('PATTERN_CHANGED','DEPENDS_ON_CHANGED_PATTERN'),
              'enrich_commits': lambda p,c: c, 'format_commit_history': lambda c: []}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), 'exec'), ns)
        report = {'semantic_changes': [{'kind':'DEPENDS_ON_CHANGED_PATTERN','constant':'COUNTRY','commit':'abc','method':'validate'}],
                  'git': {'commits': []}, 'project_path': 'sample', 'jira': 'CAS-2',
                  'source_verified_paths': [
                      {'commit':'abc','changed_method':'validate','endpoint':'POST /api/customers'},
                      {'commit':'abc','changed_method':'validate','endpoint':'PUT /api/customers/{customerId}'},
                      {'commit':'abc','changed_method':'validate','endpoint':'PATCH /api/customers/{customerId}/account-type'}],
                  'qa': {'test_cases': []}}
        out = ns['_summary'](report, attribute='countryCode')
        self.assertEqual(len(out['attribute_verified_paths']), 2)
        self.assertEqual(len(out['unverified_attribute_paths']), 1)
        self.assertIn('PATCH /api/customers/{customerId}/account-type', out['answer'].split('Unverified attribute-to-API candidates:')[1])
        self.assertNotIn('PATCH', out['answer'].split('Attribute-verified API paths:')[1].split('\n')[0])

if __name__ == '__main__': unittest.main()
