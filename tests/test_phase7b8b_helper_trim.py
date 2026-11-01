import unittest
from services.agent.mapping_contract_flow_service import _trim_evidence

class HelperTrimEvidenceTests(unittest.TestCase):
    def test_direct_trim(self):
        self.assertEqual(_trim_evidence('value.trim()', 'Mapper', [])[0], 'LOCAL_TRIM_PRESENT')

    def test_same_class_helper_is_candidate(self):
        methods = [{'class': 'Mapper', 'name': 'upper', 'body': '{ return value.trim().toUpperCase(); }'}]
        self.assertEqual(_trim_evidence('upper(source.getAccountType())', 'Mapper', methods)[0], 'HELPER_TRIM_PRESENT_CANDIDATE')

    def test_other_class_helper_not_accepted(self):
        methods = [{'class': 'Other', 'name': 'upper', 'body': '{ return value.trim(); }'}]
        self.assertEqual(_trim_evidence('upper(value)', 'Mapper', methods)[0], 'POTENTIAL_MISMATCH_NO_LOCAL_TRIM')

    def test_ambiguous_helper_not_accepted(self):
        methods = [{'class': 'Mapper', 'name': 'upper', 'body': '{ return value.trim(); }'}, {'class': 'Mapper', 'name': 'upper', 'body': '{ return value; }'}]
        self.assertEqual(_trim_evidence('upper(value)', 'Mapper', methods)[0], 'POTENTIAL_MISMATCH_NO_LOCAL_TRIM')

if __name__ == '__main__': unittest.main()
