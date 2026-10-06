from __future__ import annotations

from pathlib import Path
from typing import Any

from config import settings
from services.release_intelligence.regression_release_intelligence_service import RegressionReleaseIntelligenceService


class EngineeringAssistantService:
    """Turn evidence-grounded release intelligence into an actionable developer brief.

    This service deliberately does not invent code relationships. It composes the
    already-grounded regression/release evidence into developer actions. The LLM
    may present this payload through the Knowledge Agent, but the decisions and
    evidence in this payload are deterministic.
    """

    def __init__(self) -> None:
        self.release_intelligence = RegressionReleaseIntelligenceService()

    def status(self) -> dict[str, Any]:
        return {
            "status": "READY",
            "capability": "AI_ENGINEERING_ASSISTANT",
            "project_path": str(Path(settings.JAVA_PROJECT_PATH).resolve()),
            "supports": [
                "CURRENT_CHANGE_EXPLANATION",
                "IMPACT_SUMMARY",
                "REGRESSION_TEST_PLAN",
                "TRACEABILITY_GAP_REVIEW",
                "MAPPING_AND_REQUIREMENT_RISK_REVIEW",
                "RELEASE_READINESS_GUIDANCE",
                "DEVELOPER_NEXT_ACTIONS",
            ],
            "decision_policy": "EVIDENCE_GROUNDED_NO_AUTOMATIC_APPROVAL",
        }

    @staticmethod
    def _uniq(values: list[str]) -> list[str]:
        return list(dict.fromkeys(v for v in values if v))

    @staticmethod
    def _change_explanation(result: dict[str, Any]) -> list[dict[str, Any]]:
        changes = []
        for item in result.get("behavioral_changes") or []:
            changes.append({
                "type": item.get("type"),
                "attribute": item.get("attribute"),
                "file": item.get("file"),
                "old_condition": item.get("old_condition"),
                "new_condition": item.get("new_condition"),
                "old_operator": item.get("old_operator"),
                "new_operator": item.get("new_operator"),
                "old_threshold": item.get("old_threshold"),
                "new_threshold": item.get("new_threshold"),
                "evidence": item.get("evidence"),
            })
        return changes

    @staticmethod
    def _test_plan(result: dict[str, Any]) -> dict[str, Any]:
        scenario_tests = []
        existing_tests = []
        for rec in result.get("regression_recommendations") or []:
            scenario_tests.append({
                "scenario_code": rec.get("scenario_code"),
                "operation": rec.get("operation"),
                "impact_status": rec.get("impact_status"),
                "recommended_action": rec.get("recommended_action"),
            })
            for evidence in rec.get("automated_test_evidence") or []:
                existing_tests.append({
                    "test_class": evidence.get("test_class"),
                    "test_method": evidence.get("test_method"),
                    "file": evidence.get("file"),
                    "has_assertion": evidence.get("has_assertion"),
                    "evidence_basis": evidence.get("evidence_basis"),
                })
        return {
            "affected_scenario_tests": scenario_tests,
            "existing_test_evidence": existing_tests,
            "boundary_tests_to_add_or_verify": result.get("boundary_regression_recommendations") or [],
        }

    @staticmethod
    def _risks(result: dict[str, Any]) -> list[dict[str, Any]]:
        risks = []
        for gap in result.get("coverage_gaps") or []:
            risks.append({
                "type": gap.get("gap_type"),
                "attribute": gap.get("attribute"),
                "scenario_code": gap.get("scenario_code"),
                "endpoint": gap.get("endpoint"),
                "detail": gap.get("detail"),
            })
        for finding in result.get("evidence_consistency") or []:
            risks.append({
                "type": finding.get("type"),
                "attribute": finding.get("attribute"),
                "target": finding.get("target"),
                "detail": finding,
            })
        return risks

    def _next_actions(self, result: dict[str, Any]) -> list[str]:
        actions: list[str] = []
        summary = result.get("impact_summary") or {}
        readiness = (result.get("release_readiness") or {}).get("status")

        if result.get("boundary_regression_recommendations"):
            actions.append("Add or verify the recommended boundary-value tests for every changed condition.")
        if any((g.get("gap_type") == "NO_CAPTURED_TEST_BASELINE") for g in result.get("coverage_gaps") or []):
            actions.append("Run the affected regression tests and capture a test baseline for each impacted scenario.")
        if int(summary.get("requirement_code_mismatches") or 0) > 0:
            actions.append("Confirm the changed behavior against the authoritative requirement/Jira before release review.")
        if int(summary.get("mapping_quality_issues") or 0) > 0:
            actions.append("Resolve or explicitly review high-severity mapping/contract quality findings.")
        if int(summary.get("graph_sync_mismatches") or 0) > 0:
            actions.append("Re-sync the engineering knowledge graph and re-run the analysis.")
        if int(summary.get("failed_test_baselines") or 0) > 0:
            actions.append("Fix failed captured test evidence before release review.")
        if readiness == "READY_FOR_RELEASE_REVIEW":
            actions.append("Proceed to human release review; this status is not automatic release approval.")
        elif readiness == "NO_CHANGES":
            actions.append("No current Java Git change was detected; make or select a change before requesting change-specific guidance.")
        else:
            actions.append("Re-run Engineering Assistant after the identified evidence gaps are addressed.")
        return self._uniq(actions)

    def analyse_current_change(self, db) -> dict[str, Any]:
        result = self.release_intelligence.analyse(db)
        change_summary = result.get("change_summary") or {}
        readiness = result.get("release_readiness") or {}

        affected_operations = []
        for scenario in result.get("affected_scenarios") or []:
            affected_operations.append({
                "scenario_code": scenario.get("scenario_code"),
                "operation": f"{scenario.get('http_method') or ''} {scenario.get('endpoint') or ''}".strip(),
                "impact_status": scenario.get("impact_status"),
                "dependency_paths": scenario.get("dependency_paths") or [],
            })

        return {
            "status": "ANALYZED",
            "capability": "AI_ENGINEERING_ASSISTANT",
            "project_path": result.get("project_path"),
            "developer_brief": {
                "changed_files": change_summary.get("changed_java_files", 0),
                "changed_classes": change_summary.get("changed_classes") or [],
                "changed_methods": change_summary.get("changed_methods") or [],
                "changed_attributes": change_summary.get("changed_attributes") or [],
                "behavioral_changes": self._change_explanation(result),
                "affected_operations": affected_operations,
            },
            "test_plan": self._test_plan(result),
            "requirement_code_mismatches": result.get("requirement_code_mismatches") or [],
            "engineering_risks": self._risks(result),
            "linked_jira_ids": result.get("linked_jira_ids") or [],
            "release_readiness": readiness,
            "developer_next_actions": self._next_actions(result),
            "evidence_summary": result.get("impact_summary") or {},
            "evidence_rule": (
                "The Engineering Assistant summarizes deterministic current-change evidence. "
                "It does not create code relationships, Jira links, test results or release approval. "
                "Hybrid retrieval remains discovery evidence and release readiness remains a review gate."
            ),
        }
