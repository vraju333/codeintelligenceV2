import unittest
from services.agent.sdlc_evidence import (
    is_project_version_request, is_sdlc_history_request, compose_sdlc_report, sdlc_attribute,
)


class SdlcEvidenceTests(unittest.TestCase):
    def test_project_versions_not_mapping(self):
        q = 'Compare the latest two captured versions of the Customer Account project.'
        self.assertTrue(is_project_version_request(q))
        self.assertFalse(is_project_version_request('Compare Student Mapping V1 and V2'))

    def test_attribute_and_historical_intent(self):
        q = 'For CAS-2 and countryCode, retrieve all captured test baselines and release versions.'
        self.assertTrue(is_sdlc_history_request(q))
        self.assertEqual(sdlc_attribute(q), 'countryCode')

    def test_missing_tool_is_not_negative(self):
        report = compose_sdlc_report('For CAS-2 show release history and audit evidence', [])
        self.assertIn('`baseline_history` | NOT_QUERIED', report)
        self.assertIn('do not establish absence', report)

    def test_zero_history_is_scoped(self):
        report = compose_sdlc_report('For CAS-2 and countryCode show baseline history', [
            {'name': 'baseline_history', 'content': '{"historical_traceability":[],"history_count":0}'}])
        self.assertIn('`baseline_history` | NO_MATCH', report)
        self.assertIn('`graph_search` | NOT_QUERIED', report)

    def test_scenarios_are_candidates(self):
        report = compose_sdlc_report('For CAS-2 show release history', [
            {'name': 'scenario_impact', 'content': '{"affected_scenarios":[{"name":"POST"}]}'}])
        self.assertIn('`scenario_impact` | FOUND', report)
        self.assertIn('proposed regression scenarios', report)

    def test_error_does_not_become_no_match(self):
        report = compose_sdlc_report('For CAS-2 show release history', [
            {'name': 'baseline_history', 'content': '{"error":"database unavailable"}'}])
        self.assertIn('`baseline_history` | ERROR', report)

    def test_current_clean_git_not_history(self):
        report = compose_sdlc_report('For CAS-2 show Git change records', [])
        self.assertIn('`git_history` | NOT_QUERIED', report)
        self.assertIn('clean current Git working tree', report)


if __name__ == '__main__':
    unittest.main()
