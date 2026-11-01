import tempfile
import unittest
from pathlib import Path
from services.agent.mapping_contract_flow_service import inspect_mapping_contract

class FakeDb:
    def query(self, *_):
        raise RuntimeError('No database configured in unit test')

class GraphEvidenceTests(unittest.TestCase):
    def test_field_evidence_graph_is_not_claimed_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            java = root / 'src/main/java/demo/CustomerMapper.java'
            java.parent.mkdir(parents=True)
            java.write_text('''public class CustomerMapper {
  public void copy(Customer source, Customer target) {
    target.setFirstName(source.getFirstName().trim());
  }
}''')
            rows = [{'target_attribute':'firstName','mapping_rule':'trim'}]
            result = inspect_mapping_contract(root, 'firstName', rows, FakeDb())
            self.assertEqual(len(result['field_evidence']), 1)
            self.assertEqual(result['field_evidence'][0]['status'], 'SOURCE_OBSERVED_NOT_DATAFLOW_VERIFIED')
            self.assertEqual(result['impact_graph']['verification'], 'CANDIDATE_GRAPH_NOT_TYPE_RESOLVED')
            self.assertEqual(result['risk_summary']['verified_dataflow_paths'], 0)
            self.assertTrue(any(e['status']=='SOURCE_REFERENCE_ONLY' for e in result['impact_graph']['edges']))
            self.assertEqual(result['mapping_contract_checks'][0]['status'], 'LOCAL_TRIM_PRESENT')

if __name__ == '__main__': unittest.main()
