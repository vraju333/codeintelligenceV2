import unittest
from services.agent.impact_priority import build_priority_report

class ImpactPriorityTests(unittest.TestCase):
    def test_country_code_method_and_scenario_separation(self):
        results = [
            {'name':'static_code_analysis','content':{'attribute':'countryCode','direct_attribute_methods':['CustomerService.validate','Customer.getCountryCode','CustomerXmlMapper.toModel'], 'occurrences':[]}},
            {'name':'scenario_impact','content':{'attribute':'countryCode','affected_scenarios':[
                {'http_method':'POST','endpoint':'/api/customers','relevance':'TRANSITIVE','matched_methods':['CustomerService.validate']},
                {'http_method':'PATCH','endpoint':'/api/customers/{id}/account-type','relevance':'SHARED_FLOW','matched_methods':[]},
                {'http_method':'GET','endpoint':'/api/customers','relevance':'SHARED_FLOW','matched_methods':[]},
            ]}},
        ]
        output=build_priority_report(results,'For CAS-2 analyze countryCode')
        self.assertIn('Primary change candidates',output)
        self.assertIn('`CustomerService.validate`',output)
        self.assertIn('Attribute-only references',output)
        self.assertIn('High-priority regression candidates',output)
        self.assertIn('Conditional regression',output)
        self.assertIn('No validation-path impact established',output)
        self.assertIn('not verified Jira-to-code links',output)
    def test_no_source_evidence(self):
        self.assertEqual('',build_priority_report([{'name':'scenario_impact','content':{'attribute':'x'}}], 'CAS-2'))
    def test_no_jira(self):
        self.assertEqual('',build_priority_report([{'name':'static_code_analysis','content':{'attribute':'x'}}], 'what changed'))

if __name__=='__main__': unittest.main()
