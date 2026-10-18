import unittest
from services.agent.sdlc_evidence import compose_sdlc_report, is_project_version_request

class SdlcHistoryFollowupTests(unittest.TestCase):
    def test_version_question(self):
        self.assertTrue(is_project_version_request("Compare latest two captured versions of Customer Account project"))
    def test_missing_history_is_not_negative(self):
        result = compose_sdlc_report("For CAS-2 show release history and audit evidence", [], "")
        self.assertIn("NOT_QUERIED", result)
        self.assertIn("sdlc_audit_history", result)
    def test_project_comparison_includes_version_details(self):
        payload = '{"comparisons":[{"operation":"POST /api/customers","versions":[1,2],"status":"CHANGED","changed_snapshot_keys":["CustomerService.java"],"source_changes":[]}],"insufficient":[]}'
        result = compose_sdlc_report("Compare latest two captured versions of Customer Account project", [{"name":"project_version_comparison","content":payload}], "")
        self.assertIn("CustomerService.java", result)
        self.assertIn("CHANGED", result)
if __name__ == "__main__":
    unittest.main()
