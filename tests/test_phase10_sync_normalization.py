"""Run with: python -m unittest discover -s tests -p 'test_phase10_sync_normalization.py'"""
import unittest
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

class ProjectScopeTests(unittest.TestCase):
    def test_windows_case_and_slashes(self):
        n = EnterpriseKnowledgeSyncService.normalize_project_path
        self.assertEqual(n(r'D:\AIlearning\customer-account-service'), n('d:/ailearning/customer-account-service/'))

    def test_distinct_projects_stay_distinct(self):
        n = EnterpriseKnowledgeSyncService.normalize_project_path
        self.assertNotEqual(n(r'D:\AIlearning\customer-account-service'), n(r'D:\AIlearning\employee-demo'))

    def test_empty_scope_is_not_project(self):
        self.assertEqual(EnterpriseKnowledgeSyncService.normalize_project_path(None), '')

if __name__ == '__main__': unittest.main()
