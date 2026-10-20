import unittest
from unittest.mock import patch
from services.agent.ask_mapping_documents_service import mapping_document_intent, extract_attribute, mapping_documents_answer

class MappingIntentTests(unittest.TestCase):
    def test_mapping_request(self):
        self.assertTrue(mapping_document_intent('country code mapping documents please'))
        self.assertEqual(extract_attribute('country code mapping documents please'), 'countryCode')
    def test_git_query_not_mapping(self):
        self.assertFalse(mapping_document_intent('Which JIRA changed country code validation?'))
        self.assertFalse(mapping_document_intent('CAS-2 Git commit details'))
    def test_missing_evidence_not_replaced_with_git(self):
        with patch('services.lineage.mapping_intelligence_service.MappingIntelligenceService') as service:
            service.return_value.search.return_value={'matches': [], 'workbook_discovery':{'status':'NO_WORKBOOKS_FOUND'}}
            answer = mapping_documents_answer(None, 'country code mapping documents please')
            self.assertEqual(answer['status'], 'NOT_FOUND')
            self.assertEqual(answer['source'], 'MAPPING_DOCUMENT_EVIDENCE')
            self.assertEqual(answer['mapping_documents'], [])
    def test_mapping_rows_retained(self):
        with patch('services.lineage.mapping_intelligence_service.MappingIntelligenceService') as service:
            service.return_value.search.return_value={'matches':[{'document_id':3,'document_title':'customer-map.xlsx','source_path':'customer.countryCode','target_attribute':'COUNTRY_CODE','sheet':'Mapping','row_number':4}], 'workbook_discovery':{}}
            answer = mapping_documents_answer(None, 'country code mapping documents please')
            self.assertEqual(answer['mapping_count'], 1)
            self.assertEqual(answer['mapping_documents'][0]['row_count'], 1)
            self.assertEqual(answer['mapping_rows'][0]['row_number'], 4)
if __name__ == '__main__': unittest.main()
