import json
import unittest
from services.agent.sdlc_evidence import compose_sdlc_report

class FinalCycleTests(unittest.TestCase):
    def test_git_no_match_scoped(self):
        out = compose_sdlc_report('For CAS-2 show release history and audit evidence', [
            {'name':'git_history','content':json.dumps({'matches':[], 'scanned_commits':12})}])
        self.assertIn('`git_history` | NO_MATCH',out)
        self.assertIn('scanned 12',out)
    def test_version_diagnostic(self):
        out = compose_sdlc_report('Compare latest two captured versions of Customer Account project', [
            {'name':'project_version_comparison','content':json.dumps({'comparisons':[], 'insufficient':[], 'status':'INSUFFICIENT_CAPTURED_VERSIONS','diagnostic':'NO_CAPTURED_OPERATIONS_FOR_ACTIVE_PROJECT','captured_baseline_count':0})}])
        self.assertIn('NO_CAPTURED_OPERATIONS_FOR_ACTIVE_PROJECT',out)
    def test_baseline_jira_count(self):
        out = compose_sdlc_report('For CAS-2 show baseline history', [
            {'name':'baseline_history','content':json.dumps({'historical_traceability':[], 'jira_linked_baselines':[{'id':1}]})}])
        self.assertIn('`baseline_history` | FOUND',out)

if __name__ == '__main__': unittest.main()
