"""Regression checks for attribute-specific PATCH role inference."""
import unittest
# Load only the pure classification functions so this test runs without
# FastAPI, SQLAlchemy or a live database.
import ast
from pathlib import Path
import re
source = Path(__file__).resolve().parents[1] / 'routers' / 'requirement_impact_router.py'
module = ast.parse(source.read_text(encoding='utf-8'))
selected = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in
            {'canonical', '_endpoint_targets_attribute', 'classify_requirement_operations'}]
namespace = {'re': re}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), 'exec'), namespace)
classify_requirement_operations = namespace['classify_requirement_operations']


class PatchRoleTests(unittest.TestCase):
    def classify(self, endpoint, attribute='accountType', method='updateAccountType'):
        operations = {'potentially_related': [{
            'http_method': 'PATCH', 'endpoint': endpoint,
            'controller_method': method, 'candidate_call_paths': [{'path': [
                {'method': method}]}]}], 'other_discovered': []}
        return classify_requirement_operations(operations, [attribute], [], {'fields': [], 'status': 'NOT_CHECKED'})['role_review']

    def test_direct_patch_is_high_priority(self):
        review = self.classify('/api/customers/{customerId}/account-type')
        self.assertEqual(review['priority_operations'][0]['change_review_priority'], 'HIGH')
        self.assertEqual(review['priority_operations'][0]['requirement_roles'], ['INPUT', 'PERSISTENCE'])

    def test_unrelated_patch_is_indirect(self):
        review = self.classify('/api/customers/{customerId}/status', method='updateStatus')
        self.assertEqual(review['conditional_or_other_operations'][0]['requirement_roles'], ['INDIRECT_REGRESSION'])

    def test_no_substring_false_positive(self):
        review = self.classify('/api/customers/{customerId}/alternate-email', 'email', 'updateAlternateEmail')
        self.assertEqual(review['conditional_or_other_operations'][0]['change_review_priority'], 'LOW')


if __name__ == '__main__':
    unittest.main()
