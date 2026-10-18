"""Regression test for live Jira -> attribute evidence enrichment."""
import ast
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class JiraAttributeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "services" / "agent" / "knowledge_agent_service.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        service = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KnowledgeAgentService")
        method = next(n for n in service.body if isinstance(n, ast.FunctionDef) and n.name == "_live_jira_enrichment_calls")
        method.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        scope = {"json": json, "re": re, "Any": Any}
        exec(compile(module, str(source), "exec"), scope)
        cls.enrich = staticmethod(scope[method.name])

    def test_country_code_prose_routes_to_impact_tools(self):
        payload = {"issue": {"summary": "Update Customer Country Code validation to exactly two letters", "description": "Accept IN and US; reject IND and USA."}}
        messages = [SimpleNamespace(name="live_jira_issue", content=json.dumps(payload))]
        tools = [SimpleNamespace(name=n) for n in ("static_code_analysis", "scenario_impact", "mapping_lineage")]
        calls = self.enrich(messages, tools)
        self.assertEqual({c["name"] for c in calls}, {"static_code_analysis", "scenario_impact", "mapping_lineage"})
        self.assertTrue(all(c["args"] == {"attribute": "countryCode"} for c in calls))

    def test_unrelated_requirement_does_not_invent_country_code(self):
        messages = [SimpleNamespace(name="live_jira_issue", content=json.dumps({"issue": {"summary": "Change account type"}}))]
        tools = [SimpleNamespace(name="static_code_analysis")]
        self.assertEqual(self.enrich(messages, tools), [])

    def test_new_camel_case_identifier_is_not_country_specific(self):
        messages = [SimpleNamespace(name="live_jira_issue", content=json.dumps({"issue": {"summary": "Validate `preferredLanguage`"}}))]
        tools = [SimpleNamespace(name="static_code_analysis")]
        calls = self.enrich(messages, tools)
        self.assertEqual(calls[0]["args"], {"attribute": "preferredLanguage"})


if __name__ == "__main__":
    unittest.main()
