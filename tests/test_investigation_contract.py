import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.agent.investigation_contract import build_investigation

class InvestigationContractTests(unittest.TestCase):
    def test_preserves_candidate_and_verified_boundaries(self):
        answer = {'status': 'FOUND', 'answer': 'evidence',
                  'attribute_verified_paths': [{'method': 'CustomerService.create'}],
                  'predictive_api_candidates': [{'endpoint': '/customers'}],
                  'predictive_scenario_candidates': [{'scenario_code': 'S1'}],
                  'predictive_impact': {'regression_recommendations': [{'test': 'review candidate'}]}}
        report = build_investigation('change countryCode', '/repo', answer)
        by_key = {e['source_field']: e for e in report['evidence']}
        self.assertEqual(by_key['attribute_verified_paths']['classification'], 'SOURCE_VERIFIED')
        self.assertEqual(by_key['predictive_api_candidates']['classification'], 'CANDIDATE')
        self.assertEqual(by_key['predictive_impact.regression_recommendations']['classification'], 'PREDICTIVE_NOT_EXECUTED')
        self.assertEqual(report['coverage']['status'], 'NOT_DETERMINED')
        self.assertFalse(report['execution_verified'])

    def test_no_evidence_is_not_a_coverage_gap(self):
        report = build_investigation('impact?', '/repo', {'answer': 'No evidence'})
        self.assertEqual(report['evidence'], [])
        self.assertTrue(report['warnings'])
        self.assertEqual(report['coverage']['status'], 'NOT_DETERMINED')

if __name__ == '__main__': unittest.main()
