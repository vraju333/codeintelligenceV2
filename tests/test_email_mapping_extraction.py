import unittest
from services.agent.ask_mapping_documents_service import extract_attribute

class MappingExtractionTests(unittest.TestCase):
    def test_plain_email(self):
        self.assertEqual(extract_attribute('mapping documents for email'), 'email')
    def test_account_type_unchanged(self):
        self.assertEqual(extract_attribute('mapping documents for accountType'), 'accountType')
    def test_country_code_unchanged(self):
        self.assertEqual(extract_attribute('mapping documents for countryCode'), 'countryCode')
    def test_no_attribute(self):
        self.assertEqual(extract_attribute('show mapping documents'), '')

if __name__ == '__main__':
    unittest.main()
