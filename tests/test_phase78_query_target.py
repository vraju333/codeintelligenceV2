import unittest
from services.agent.ask_mapping_documents_service import extract_attribute

class QueryTargetTests(unittest.TestCase):
    def test_explicit_field_wins_over_endpoint(self):
        query = ('Show the impact of firstName mapping on PATCH '
                 '/api/customers/{customerId}/account-type. '
                 'Include the mapping lineage and affected Java methods.')
        self.assertEqual(extract_attribute(query), 'firstName')

    def test_existing_account_type_question(self):
        self.assertEqual(extract_attribute('Show mapping for accountType'), 'accountType')

    def test_country_code(self):
        self.assertEqual(extract_attribute('Show mapping impact of countryCode'), 'countryCode')

if __name__ == '__main__':
    unittest.main()
