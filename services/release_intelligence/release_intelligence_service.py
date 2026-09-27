from services.regression.regression_impact_service import RegressionImpactService
from services.test_analysis.test_code_analysis_service import TestCodeAnalysisService


class ReleaseIntelligenceService:
    """Phase C: change impact + test evidence + regression recommendation + gaps."""

    def __init__(self):
        self.regression = RegressionImpactService()
        self.test_analysis = TestCodeAnalysisService()

    def analyse(self, db):
        impact = self.regression.analyse(db)
        tests = self.test_analysis.analyse()
        affected = impact.get("affected_scenarios") or []

        recommendations = []
        gaps = []
        failed = 0
        covered = 0
        jira_ids = set()

        for scenario in affected:
            static_tests = self.test_analysis.evidence_for_scenario(scenario, tests)
            baselines = scenario.get("test_baselines") or []
            failed_here = [
                x for x in baselines
                if str(x.get("status") or "").upper() == "FAIL"
            ]
            failed += len(failed_here)
            jira_ids.update(str(x) for x in (scenario.get("jira_ids") or []) if x)

            has_baseline = bool(baselines)
            has_test_code = bool(static_tests)
            if has_baseline or has_test_code:
                covered += 1

            reasons = []
            if scenario.get("matched_methods"):
                reasons.append("Changed method intersects the stored operation flow")
            elif scenario.get("matched_classes"):
                reasons.append("Changed class intersects the stored operation flow")
            if has_baseline:
                reasons.append("Existing captured test baseline is available")
            if has_test_code:
                reasons.append("JUnit/static test-source evidence was found")

            recommendations.append({
                "scenario_id": scenario.get("scenario_id"),
                "scenario_code": scenario.get("scenario_code"),
                "http_method": scenario.get("http_method"),
                "endpoint": scenario.get("endpoint"),
                "impact_status": scenario.get("impact_status"),
                "reasons": reasons,
                "test_baselines": baselines,
                "automated_test_evidence": static_tests,
                "recommended_action": (
                    "Investigate failed evidence, then rerun affected regression tests"
                    if failed_here else
                    "Rerun affected regression tests and capture the release test baseline"
                ),
            })

            if not has_baseline:
                gaps.append({
                    "scenario_id": scenario.get("scenario_id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "gap_type": "NO_CAPTURED_TEST_BASELINE",
                    "endpoint": f"{scenario.get('http_method', '')} {scenario.get('endpoint', '')}".strip(),
                    "detail": "Code impact exists, but no captured test baseline is linked to this affected scenario.",
                })
            if not has_test_code:
                gaps.append({
                    "scenario_id": scenario.get("scenario_id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "gap_type": "NO_AUTOMATED_TEST_SOURCE_EVIDENCE",
                    "endpoint": f"{scenario.get('http_method', '')} {scenario.get('endpoint', '')}".strip(),
                    "detail": "No matching JUnit/static test-source evidence was found for the affected code flow.",
                })

        total = len(affected)
        return {
            "status": impact.get("status"),
            "summary": {
                "changed_files": impact.get("total_changed_java_files", 0),
                "changed_methods": len(impact.get("changed_methods") or []),
                "changed_attributes": len(impact.get("changed_attributes") or []),
                "affected_scenarios": total,
                "scenarios_with_test_evidence": covered,
                "coverage_gaps": len(gaps),
                "failed_test_baselines": failed,
                "linked_jiras": len(jira_ids),
                "junit_test_methods_discovered": tests.get("test_methods", 0),
            },
            "changed_classes": impact.get("changed_classes") or [],
            "changed_methods": impact.get("changed_methods") or [],
            "changed_attributes": impact.get("changed_attributes") or [],
            "regression_recommendations": recommendations,
            "coverage_gaps": gaps,
            "linked_jira_ids": sorted(jira_ids),
            "test_code_analysis": tests,
            "release_readiness": {
                "blocking_failed_test_evidence": failed,
                "missing_evidence_items": len(gaps),
                "message": (
                    "Release review has failed test evidence that requires investigation."
                    if failed else
                    "No failed captured test baseline was found; review the listed coverage gaps before release approval."
                    if gaps else
                    "Affected scenarios have test evidence and no failed captured baseline was found."
                ),
                "note": "This is evidence summary, not an automated release approval or risk score.",
            },
        }
