import unittest
from services.agent.evidence_answer import compose_evidence_answer

class EvidenceAnswerTests(unittest.TestCase):
    def test_no_contradictory_scenarios_or_methods(self):
        agent = '''### JIRA CAS-2 Overview
Valid IN, US; invalid USA.
### 1. Java Methods Impacted
All getters need changing.
### 2. XML-to-Model Mappings
Row 6: /CustomerAccount/CountryCode to Customer.countryCode.
### 3. Regression Scenarios Impacted
GET and DELETE are impacted.
### 4. Existing Test Baselines
No explicit baseline was found. However, all scenarios listed above should be included in regression testing.
### Summary of Evidence
GET is impacted.'''
        report = '### Deterministic impact prioritization\n- **No validation-path impact established:** `GET /api/customers`, `DELETE /api/customers/{id}`'
        result = compose_evidence_answer(report, agent)
        self.assertIn('Row 6', result)
        self.assertIn('No JIRA-linked test baseline is established', result)
        self.assertIn('Valid IN', result)
        self.assertNotIn('GET and DELETE are impacted', result)
        self.assertNotIn('All getters need changing', result)
        self.assertNotIn('all scenarios listed above should be included', result)
        self.assertIn('GET and DELETE are not automatically regression-required', result)
    def test_fallback_unchanged_without_report(self):
        self.assertEqual('original', compose_evidence_answer('', 'original'))
    def test_unknown_sections_fail_closed(self):
        self.assertNotIn('unsupported', compose_evidence_answer('evidence', '### Speculative Impact\nunsupported'))
    def test_baseline_guidance_when_model_omits_section(self):
        answer = compose_evidence_answer('source evidence', '### JIRA CAS-2 Overview\nCountry code.')
        self.assertIn('Proposed regression tests (not existing baselines)', answer)
        self.assertIn('Test PATCH only if', answer)
    def test_does_not_claim_absence_of_all_baselines(self):
        answer = compose_evidence_answer('source evidence', '### Existing Test Baselines\nNo baselines exist anywhere.')
        self.assertNotIn('No baselines exist anywhere', answer)
        self.assertIn('does not prove that no baseline exists', answer)

if __name__ == '__main__':
    unittest.main()
