from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline
from db_models import Scenario
from services.regression.git_diff_service import GitDiffService


class RegressionImpactService:
    """Compare current Java changes against every active scenario baseline.

    Impact is calculated project-wide. A scenario is DIRECTLY_AFFECTED when a
    changed Class.method is present in its stored endpoint flow. It is
    POSSIBLY_AFFECTED when a changed class is part of the baseline dependency
    set but the exact method is not present in the stored flow. All other
    registered scenarios are returned as UNAFFECTED so the UI/report can show
    the complete blast radius instead of only the selected scenario.
    """

    def __init__(self):
        self.git_diff_service = GitDiffService()

    def analyse(self, db: Session):
        changes = self.git_diff_service.analyse_changes()

        active_baselines = (
            db.query(ScenarioBaseline)
            .filter(ScenarioBaseline.is_active.is_(True))
            .all()
        )
        all_scenarios = db.query(Scenario).order_by(Scenario.id).all()

        changed_classes = set()
        changed_methods = []
        changed_method_keys = set()

        for changed_file in changes.get("changed_files", []):
            class_name = changed_file.get("class_name")
            if not class_name:
                continue
            changed_classes.add(class_name)

            for method in changed_file.get("changed_methods", []):
                method_name = method.get("method_name")
                item = {
                    "class_name": class_name,
                    "method_name": method_name,
                    "changed_lines": method.get("changed_lines", []),
                }
                changed_methods.append(item)
                if method_name:
                    changed_method_keys.add(f"{class_name}.{method_name}")

        baseline_by_scenario = {
            baseline.scenario_id: baseline for baseline in active_baselines
        }

        directly_affected = []
        possibly_affected = []
        unaffected = []

        for scenario in all_scenarios:
            baseline = baseline_by_scenario.get(scenario.id)

            if not baseline:
                unaffected.append({
                    "scenario_id": scenario.id,
                    "scenario_code": scenario.scenario_code,
                    "http_method": scenario.http_method,
                    "endpoint": scenario.endpoint,
                    "baseline_version": None,
                    "impact_status": "NO_BASELINE",
                    "matched_classes": [],
                    "changed_methods": [],
                    "dependency_paths": [],
                    "reason": "No active baseline has been captured for this scenario.",
                })
                continue

            flow_classes, flow_methods = self._extract_flow_dependencies(
                baseline.endpoint_flow
            )
            declared_classes = set(baseline.involved_classes or [])
            dependency_classes = declared_classes | flow_classes

            matched_classes = sorted(dependency_classes & changed_classes)
            declared_matched_classes = sorted(declared_classes & changed_classes)
            matched_method_keys = sorted(flow_methods & changed_method_keys)
            scenario_methods = [
                method for method in changed_methods
                if f"{method['class_name']}.{method.get('method_name')}" in matched_method_keys
            ]

            dependency_paths = self._build_dependency_paths(
                endpoint_flow=baseline.endpoint_flow,
                endpoint_label=f"{baseline.http_method} {baseline.endpoint}",
                changed_classes=changed_classes,
                changed_method_keys=changed_method_keys,
            )

            base_result = {
                "scenario_id": baseline.scenario_id,
                "scenario_code": baseline.scenario_code,
                "baseline_version": baseline.baseline_version,
                "http_method": baseline.http_method,
                "endpoint": baseline.endpoint,
                "matched_classes": matched_classes,
                "declared_matched_classes": declared_matched_classes,
                "matched_methods": matched_method_keys,
                "changed_methods": scenario_methods,
                "dependency_paths": dependency_paths,
            }

            if matched_method_keys or declared_matched_classes:
                direct_methods = [
                    method for method in changed_methods
                    if method["class_name"] in declared_matched_classes
                    or f"{method['class_name']}.{method.get('method_name')}" in matched_method_keys
                ]
                directly_affected.append({
                    **base_result,
                    "impact_status": "DIRECTLY_AFFECTED",
                    "changed_methods": direct_methods,
                    "reason": (
                        "Changed code intersects an explicit scenario dependency"
                        " or a method in the stored execution flow."
                    ),
                })
            elif matched_classes:
                class_methods = [
                    method for method in changed_methods
                    if method["class_name"] in matched_classes
                ]
                possibly_affected.append({
                    **base_result,
                    "impact_status": "POSSIBLY_AFFECTED",
                    "changed_methods": class_methods,
                    "reason": "Changed class(es) are used by the scenario, but the exact changed method is not recorded in the baseline flow.",
                })
            else:
                unaffected.append({
                    **base_result,
                    "impact_status": "UNAFFECTED",
                    "reason": "No changed class or method intersects this scenario baseline.",
                })

        affected_scenarios = directly_affected + possibly_affected

        return {
            "status": (
                "CHANGES_DETECTED"
                if changes.get("total_changed_java_files", 0) > 0
                else "NO_CHANGES"
            ),
            "total_changed_java_files": changes.get("total_changed_java_files", 0),
            "changed_classes": sorted(changed_classes),
            "changed_methods": changed_methods,
            "total_registered_scenarios": len(all_scenarios),
            "total_directly_affected": len(directly_affected),
            "total_possibly_affected": len(possibly_affected),
            "total_unaffected": len(unaffected),
            "total_affected_scenarios": len(affected_scenarios),
            "directly_affected": directly_affected,
            "possibly_affected": possibly_affected,
            "unaffected_scenarios": unaffected,
            "affected_scenarios": affected_scenarios,
            "git_changes": changes,
        }

    def _build_dependency_paths(
        self,
        endpoint_flow,
        endpoint_label: str,
        changed_classes: set[str],
        changed_method_keys: set[str],
    ) -> list[dict]:
        ordered = self._ordered_flow_methods(endpoint_flow)
        if not ordered:
            return []

        paths = []
        for index, method in enumerate(ordered):
            class_name = method.split(".", 1)[0]
            if method not in changed_method_keys and class_name not in changed_classes:
                continue

            # Show enough upstream/downstream context to explain why the scenario
            # depends on the changed code without dumping the entire flow.
            start = max(0, index - 3)
            end = min(len(ordered), index + 4)
            chain = ordered[start:end]
            chain = list(dict.fromkeys([endpoint_label, *chain]))
            paths.append({
                "changed_symbol": method,
                "path": chain,
            })

        # A class can be explicitly declared as involved even when an older stored
        # flow does not contain a method for it. Keep that evidence visible.
        covered_classes = {
            item["changed_symbol"].split(".", 1)[0]
            for item in paths
        }
        for class_name in sorted(changed_classes - covered_classes):
            if class_name in self._extract_flow_dependencies(endpoint_flow)[0]:
                paths.append({
                    "changed_symbol": class_name,
                    "path": [endpoint_label, class_name],
                })

        return paths[:8]

    def _ordered_flow_methods(self, endpoint_flow) -> list[str]:
        result = []

        def add(class_name, method_name):
            if class_name and method_name:
                value = f"{class_name}.{method_name}"
                if value not in result:
                    result.append(value)

        def walk(value):
            if isinstance(value, dict):
                add(value.get("class_name"), value.get("method_name"))
                # Prefer call order when this is a flow node.
                calls = value.get("calls")
                if isinstance(calls, list):
                    for child in calls:
                        walk(child)
                for key, child in value.items():
                    if key == "calls":
                        continue
                    if isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str) and "." in value and " " not in value:
                parts = value.split(".", 1)
                if len(parts) == 2:
                    add(parts[0], parts[1])

        walk(endpoint_flow or {})
        return result

    def _extract_flow_dependencies(self, endpoint_flow):
        classes = set()
        methods = set()

        def walk(value):
            if isinstance(value, dict):
                class_name = value.get("class_name")
                method_name = value.get("method_name")
                if class_name:
                    classes.add(str(class_name))
                    if method_name:
                        methods.add(f"{class_name}.{method_name}")

                for child in value.values():
                    walk(child)

            elif isinstance(value, list):
                for child in value:
                    walk(child)

            elif isinstance(value, str):
                # simplified_flow stores labels such as EmployeeMapper.toEntity
                if "." in value and " " not in value:
                    parts = value.split(".", 1)
                    if len(parts) == 2 and parts[0] and parts[1]:
                        classes.add(parts[0])
                        methods.add(value)

        walk(endpoint_flow or {})
        return classes, methods
