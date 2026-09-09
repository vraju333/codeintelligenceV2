from datetime import datetime, timezone

from sqlalchemy.orm import Session

from services.regression.regression_impact_service import RegressionImpactService


class RegressionReportService:

    def __init__(self):
        self.regression_impact_service = RegressionImpactService()

    def generate(self, db: Session):
        regression = self.regression_impact_service.analyse(db)
        changed_methods = regression.get("changed_methods", [])
        changed_classes = regression.get("changed_classes", [])

        all_scenarios = []
        for key in ("directly_affected", "possibly_affected", "unaffected_scenarios"):
            for scenario in regression.get(key, []):
                all_scenarios.append(self._build_scenario_report(scenario))

        report = {
            "report_type": "REGRESSION_ANALYSIS",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": self._determine_report_status(regression),
            "summary": {
                "changed_java_files": regression.get("total_changed_java_files", 0),
                "changed_classes": len(changed_classes),
                "changed_methods": len(changed_methods),
                "registered_scenarios": regression.get("total_registered_scenarios", 0),
                "directly_affected": regression.get("total_directly_affected", 0),
                "possibly_affected": regression.get("total_possibly_affected", 0),
                "unaffected": regression.get("total_unaffected", 0),
                "affected_scenarios": regression.get("total_affected_scenarios", 0),
            },
            "changed_classes": changed_classes,
            "changed_methods": changed_methods,
            "scenarios": all_scenarios,
            "directly_affected": [self._build_scenario_report(s) for s in regression.get("directly_affected", [])],
            "possibly_affected": [self._build_scenario_report(s) for s in regression.get("possibly_affected", [])],
            "unaffected_scenarios": [self._build_scenario_report(s) for s in regression.get("unaffected_scenarios", [])],
        }
        report["report_text"] = self._build_text_report(report)
        return report

    def _build_scenario_report(self, scenario: dict):
        changed_methods = scenario.get("changed_methods", [])
        method_names = []
        for method in changed_methods:
            class_name = method.get("class_name", "")
            method_name = method.get("method_name", "")
            if class_name and method_name:
                method_names.append(f"{class_name}.{method_name}")

        return {
            "scenario_id": scenario.get("scenario_id"),
            "scenario_code": scenario.get("scenario_code"),
            "baseline_version": scenario.get("baseline_version"),
            "http_method": scenario.get("http_method"),
            "endpoint": scenario.get("endpoint"),
            "impact_status": scenario.get("impact_status"),
            "matched_classes": scenario.get("matched_classes", []),
            "matched_methods": scenario.get("matched_methods", []),
            "changed_methods": changed_methods,
            "changed_method_names": method_names,
            "reason": scenario.get("reason") or self._build_reason(scenario),
        }

    def _build_reason(self, scenario: dict):
        matched_classes = scenario.get("matched_classes", [])
        if not matched_classes:
            return "No changed classes matched this scenario baseline."
        return "The scenario baseline depends on changed class(es): " + ", ".join(matched_classes) + "."

    def _determine_report_status(self, regression: dict):
        if regression.get("status") == "NO_CHANGES":
            return "NO_CHANGES"
        if regression.get("total_affected_scenarios", 0) > 0:
            return "REGRESSION_RISK_DETECTED"
        return "CHANGES_WITH_NO_REGISTERED_IMPACT"

    def _build_text_report(self, report: dict):
        summary = report["summary"]
        return "\n".join([
            "REGRESSION ANALYSIS REPORT",
            "=" * 30,
            f"Status: {report['status']}",
            f"Generated At: {report['generated_at']}",
            "",
            f"Changed Java Files: {summary['changed_java_files']}",
            f"Changed Classes: {summary['changed_classes']}",
            f"Changed Methods: {summary['changed_methods']}",
            f"Registered Scenarios: {summary['registered_scenarios']}",
            f"Directly Affected: {summary['directly_affected']}",
            f"Possibly Affected: {summary['possibly_affected']}",
            f"Unaffected / No Baseline: {summary['unaffected']}",
        ])
