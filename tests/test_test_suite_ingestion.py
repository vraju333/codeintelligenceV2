import csv
import io
import json
import unittest
from services.knowledge.test_suite_service import parse_test_cases

class TestSuiteIngestionTests(unittest.TestCase):
    def test_csv_with_provenance(self):
        raw = 'Test ID,Scenario,Input countryCode,Expected outcome,JIRA,Java attribute\nT1,Accept IN,IN,Accepted,CAS-2,Customer.countryCode\n'
        cases = parse_test_cases('suite.csv', raw.encode())
        self.assertEqual(cases[0]['input'], 'IN')
        self.assertEqual(cases[0]['attribute'], 'countryCode')
        self.assertEqual(cases[0]['jira'], 'CAS-2')
        self.assertEqual(cases[0]['execution_status'], 'NOT_VERIFIED')

    def test_json_does_not_fabricate_execution(self):
        data = json.dumps({'test_cases': [{'test_id':'T2','scenario':'Reject USA','input':'USA','expected':'Rejected'}]})
        cases = parse_test_cases('cases.json', data.encode(), 'CAS-2')
        self.assertEqual(cases[0]['expected'], 'Rejected')
        self.assertEqual(cases[0]['execution_status'], 'NOT_VERIFIED')

    def test_mapping_only_rows_are_ignored(self):
        cases = parse_test_cases('map.csv', b'XML tag,Java attribute\n/CountryCode,countryCode\n')
        self.assertEqual(cases, [])

    def test_unsupported_file(self):
        with self.assertRaises(ValueError):
            parse_test_cases('file.exe', b'hello')

if __name__ == '__main__':
    unittest.main()
