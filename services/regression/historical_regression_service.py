from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline
from services.regression.flow_comparison_service import (
    FlowComparisonService
)
from services.regression.git_diff_service import (
    GitDiffService
)


class HistoricalRegressionService:

    def __init__(self):

        self.git_diff_service = (
            GitDiffService()
        )

        self.flow_comparison_service = (
            FlowComparisonService()
        )

    def analyse(
        self,
        db: Session,
        scenario_id: int,
        current_flow: dict
    ):

        baseline = (
            db.query(ScenarioBaseline)
            .filter(
                ScenarioBaseline.scenario_id
                == scenario_id,
                ScenarioBaseline.is_active
                .is_(True)
            )
            .first()
        )

        if not baseline:

            return {
                "status":
                    "BASELINE_NOT_FOUND",
                "scenario_id":
                    scenario_id
            }

        git_changes = (
            self.git_diff_service
            .analyse_changes()
        )

        changed_methods = (
            self._extract_changed_methods(
                git_changes
            )
        )

        current_methods = (
            self.flow_comparison_service
            .extract_methods(
                current_flow
            )
        )

        current_method_set = set(
            current_methods
        )

        exact_impacts = []

        for changed in changed_methods:

            full_method_name = (
                f"{changed['class_name']}."
                f"{changed['method_name']}"
            )

            if (
                full_method_name
                in current_method_set
            ):

                exact_impacts.append(
                    {
                        **changed,
                        "full_method_name":
                            full_method_name,
                        "on_scenario_path":
                            True
                    }
                )

        flow_comparison = (
            self.flow_comparison_service
            .compare(
                baseline_flow=
                    baseline.endpoint_flow,
                current_flow=
                    current_flow
            )
        )

        impact_status = (
            self._determine_status(
                exact_impacts,
                flow_comparison
            )
        )

        return {
            "scenario_id":
                baseline.scenario_id,

            "scenario_code":
                baseline.scenario_code,

            "baseline_version":
                baseline.baseline_version,

            "http_method":
                baseline.http_method,

            "endpoint":
                baseline.endpoint,

            "impact_status":
                impact_status,

            "changed_methods":
                changed_methods,

            "scenario_methods":
                current_methods,

            "exact_method_impacts":
                exact_impacts,

            "flow_comparison":
                flow_comparison
        }

    def _extract_changed_methods(
        self,
        git_changes: dict
    ):

        result = []

        for changed_file in git_changes.get(
            "changed_files",
            []
        ):

            class_name = changed_file.get(
                "class_name"
            )

            for method in changed_file.get(
                "changed_methods",
                []
            ):

                result.append(
                    {
                        "class_name":
                            class_name,

                        "method_name":
                            method.get(
                                "method_name"
                            ),

                        "changed_lines":
                            method.get(
                                "changed_lines",
                                []
                            )
                    }
                )

        return result

    def _determine_status(
        self,
        exact_impacts: list,
        flow_comparison: dict
    ):

        if flow_comparison.get(
            "flow_changed"
        ):
            return "FLOW_CHANGED"

        if exact_impacts:
            return (
                "HIGH_CONFIDENCE_IMPACT"
            )

        return "NO_DIRECT_METHOD_IMPACT"