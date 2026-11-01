import unittest
from services.agent.mapping_contract_flow_service import _receiver_call_candidate, _deduplicate_edges

class ReceiverGuardTests(unittest.TestCase):
    def setUp(self):
        self.target = {'name': 'updateAccountType', 'class': 'CustomerService'}
    def test_unrelated_same_name_rejected(self):
        caller = {'body': '{ other.updateAccountType(value); }'}
        self.assertFalse(_receiver_call_candidate(caller, self.target, 'class Controller { CustomerService service; }'))
    def test_typed_receiver_accepted_as_candidate(self):
        caller = {'body': '{ service.updateAccountType(value); }'}
        self.assertTrue(_receiver_call_candidate(caller, self.target, 'class Controller { CustomerService service; }'))
    def test_distinct_line_evidence_preserved(self):
        a = {'from':'a','to':'b','status':'SOURCE_REFERENCE_ONLY','file':'A.java','line':10}
        b = dict(a, line=11)
        self.assertEqual(_deduplicate_edges([a,a,b]), [a,b])

if __name__ == '__main__':
    unittest.main()
