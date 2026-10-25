from services.regression.regression_impact_service import RegressionImpactService
from services.test_analysis.test_code_analysis_service import TestCodeAnalysisService
from services.release_intelligence.jira_relevance_service import JiraRelevanceService


class ReleaseIntelligenceService:
    """Phase C: change impact + test evidence + regression recommendation + gaps."""

    def __init__(self):
        self.regression = RegressionImpactService()
        self.test_analysis = TestCodeAnalysisService()
        self.jira_relevance = JiraRelevanceService()

    def analyse(self, db, mode: str = "working", base_ref: str | None = None, target_ref: str | None = None, impact: dict | None = None):
        impact = impact if impact is not None else self.regression.analyse(db, mode=mode, base_ref=base_ref, target_ref=target_ref)
        tests = self.test_analysis.analyse()
        affected = impact.get("affected_scenarios") or []

        recommendations = []
        gaps = []
        failed = 0
        covered = 0
        jira_ids = set()
        unverified_test_scenarios = 0

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
            # A class/attribute in test setup is NOT evidence of changed behavior.
            # Require a direct invocation of the changed production method AND
            # an assertion before classifying static evidence as targeted.
            changed_keys = {
                f"{m.get('class_name')}.{m.get('method_name')}"
                for m in (scenario.get('changed_methods') or impact.get('changed_methods') or [])
                if m.get('class_name') and m.get('method_name')
            }
            targeted_tests = [
                t for t in static_tests
                if t.get('has_assertion') and set(t.get('matched_calls') or []) & changed_keys
            ]
            has_targeted_test = bool(targeted_tests)
            if has_baseline or has_targeted_test:
                covered += 1
            elif static_tests:
                unverified_test_scenarios += 1

            reasons = []
            if scenario.get("matched_methods"):
                reasons.append("Changed method intersects the stored operation flow")
            elif scenario.get("matched_classes"):
                reasons.append("Changed class intersects the stored operation flow")
            if has_baseline:
                reasons.append("Existing captured test baseline is available")
            if static_tests:
                reasons.append("Related JUnit test source found; changed behavior is not verified" if not has_targeted_test else "JUnit test directly invokes changed method (static evidence only)")

            recommendations.append({
                "scenario_id": scenario.get("scenario_id"),
                "scenario_code": scenario.get("scenario_code"),
                "http_method": scenario.get("http_method"),
                "endpoint": scenario.get("endpoint"),
                "impact_status": scenario.get("impact_status"),
                "reasons": reasons,
                "test_baselines": baselines,
                "jira_ids": scenario.get("jira_ids") or [],
                "explicit_jira_ids": scenario.get("jira_ids") or [],
                "targeted_static_test_evidence": targeted_tests,
                "test_evidence_assessment": "TARGETED_STATIC_METHOD_CALL" if has_targeted_test else "RELATED_ONLY_NOT_BEHAVIOR_VERIFIED" if static_tests else "NO_RELATED_TEST_FOUND",
                "automated_test_evidence": static_tests,
                "recommended_action": (
                    "Investigate failed evidence, then rerun affected regression tests"
                    if failed_here else
                    "Rerun relevant regression tests and record execution evidence"
                ),
            })

            if not has_targeted_test and not has_baseline:
                gaps.append({
                    "scenario_id": scenario.get("scenario_id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "gap_type": "NO_TARGETED_CHANGED_METHOD_TEST_EVIDENCE",
                    "endpoint": f"{scenario.get('http_method', '')} {scenario.get('endpoint', '')}".strip(),
                    "detail": "No assertion-bearing JUnit test directly invokes the changed method. Related setup or shared-class tests do not verify changed behavior; review and run a targeted test.",
                })

        total = len(affected)
        inferred_jiras = self.jira_relevance.find(
            db, impact.get('changed_attributes') or [], impact.get('changed_classes') or [], jira_ids
        )
        # Inferred relevance is project-scoped and deliberately not written to
        # scenario.jira_id, baselines, or the explicit linked_jira_ids field.
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
                "scenarios_with_only_related_test_source": unverified_test_scenarios,
                "inferred_jira_candidates": len(inferred_jiras),
            },
            "changed_classes": impact.get("changed_classes") or [],
            "changed_methods": impact.get("changed_methods") or [],
            "changed_attributes": impact.get("changed_attributes") or [],
            "regression_recommendations": recommendations,
            "coverage_gaps": gaps,
            "linked_jira_ids": sorted(jira_ids),
            "inferred_related_jiras": inferred_jiras,
            "test_code_analysis": tests,
            "release_readiness": {
                "blocking_failed_test_evidence": failed,
                "missing_evidence_items": len(gaps),
                "message": (
                    "Release review has failed test evidence that requires investigation."
                    if failed else
                    "No failed captured test baseline was found; review the listed coverage gaps before release approval."
                    if gaps else
                    "No affected scenarios were detected." if not affected else "Affected scenarios have targeted static test evidence or captured baselines; runtime verification is still required."
                ),
                "note": "This is evidence summary, not an automated release approval or risk score.",
            },
        }
