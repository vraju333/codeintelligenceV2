"""Static regression guard: hypothetical impacts must have a distinct candidate channel."""
import ast
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class PredictiveContractTests(unittest.TestCase):
    def test_combined_response_separates_predictive_and_verified(self):
        source = (ROOT/'routers/knowledge_agent_router.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'combined_attribute_impact')
        literals = {n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for field in ('predictive_api_candidates', 'predictive_scenario_candidates', 'attribute_verified_paths', 'predictive_evidence_status'):
            self.assertIn(field, literals)
        self.assertIn('LEXICAL_CANDIDATES_NOT_VERIFIED', literals)

    def test_predictive_is_not_git_comparison(self):
        source = (ROOT/'services/agent/project_attribute_impact_service.py').read_text(encoding='utf-8')
        self.assertIn('A hypothetical mapping change cannot be treated as an actual Git diff.', source)
        self.assertIn('PROJECT_AND_ENDPOINT_MATCH_NOT_TEST_EXECUTION', (ROOT/'services/agent/mapping_contract_flow_service.py').read_text(encoding='utf-8'))

if __name__ == '__main__': unittest.main()
