import unittest
from unittest.mock import patch
from services.agent.ask_mapping_documents_service import mapping_document_intent, mapping_comparison_intent, mapping_comparison_answer

class MappingIntentTests(unittest.TestCase):
    def test_documents(self):
        self.assertTrue(mapping_document_intent('country code mapping documents please'))
    def test_explanation(self):
        self.assertTrue(mapping_document_intent('How is country code mapped?'))
    def test_comparison(self):
        self.assertTrue(mapping_comparison_intent('Does country code mapping match the Java validation?'))
        self.assertFalse(mapping_document_intent('Does country code mapping match the Java validation?'))
    def test_git_kept_separate(self):
        self.assertFalse(mapping_document_intent('Which Jira changed country code validation?'))
    @patch('services.agent.ask_mapping_documents_service.mapping_documents_answer')
    def test_missing_mapping_never_falls_back_to_git(self, mapping):
        mapping.return_value={'attribute':'countryCode','mapping_rows':[],'mapping_documents':[]}
        result=mapping_comparison_answer(None,'Does country code mapping match Java validation?')
        self.assertEqual(result['comparison_status'],'NOT_VERIFIED')
        self.assertIsNone(result['code_evidence'])

if __name__=='__main__': unittest.main()
