import re
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline, OperationBaseline
from db_models import Scenario
from services.regression.git_diff_service import GitDiffService
from services.scenario.scenario_service import ScenarioService
from services.flow.endpoint_flow_service import EndpointFlowService
from repositories.scenario_baseline_repository import ScenarioBaselineRepository
from db_models import JiraKnowledge
from config import settings
from pathlib import Path


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
        self.baselines = ScenarioBaselineRepository()

    def analyse(self, db: Session):
        changes = self.git_diff_service.analyse_changes()

        # Visible baselines are operation-level.  Every scenario/test case under
        # the same HTTP operation reuses that active baseline/version.
        project_path = str(Path(settings.JAVA_PROJECT_PATH).resolve())
        operation_baselines = (
            db.query(OperationBaseline)
            .filter(
                OperationBaseline.project_path == project_path,
                OperationBaseline.is_active.is_(True),
            )
            .all()
        )
        operation_by_key = {
            (str(b.http_method).upper(), str(b.endpoint)): b
            for b in operation_baselines
        }

        # Backward compatibility for scenarios captured before operation-level
        # baselines existed.
        legacy_baselines = (
            db.query(ScenarioBaseline)
            .filter(ScenarioBaseline.is_active.is_(True))
            .all()
        )
        legacy_by_scenario = {b.scenario_id: b for b in legacy_baselines}
        all_scenarios = ScenarioService().get_all_for_active_project(db)

        # Current-source endpoint evidence. This is intentionally independent of
        # captured operation baselines so a newly/never-baselined scenario can
        # still be identified as impacted by a changed controller method.
        live_controller_by_endpoint = {}
        try:
            for ep in EndpointFlowService().discover_endpoints():
                live_key = (
                    str(ep.get("http_method") or "").upper(),
                    self._canonical_endpoint(ep.get("endpoint")),
                )
                live_controller_by_endpoint.setdefault(live_key, set()).add(
                    f"{ep.get('class_name')}.{ep.get('method_name')}"
                )
        except Exception:
            live_controller_by_endpoint = {}

        changed_classes = set()
        changed_methods = []
        changed_method_keys = set()
        changed_symbols = []
        changed_attributes = set()
        for changed_file in changes.get("changed_files", []):
            class_name = changed_file.get("class_name")
            if not class_name:
                continue
            changed_classes.add(class_name)
            for symbol in changed_file.get("changed_symbols", []):
                changed_symbols.append(symbol)
                if symbol.get("symbol_type") == "ATTRIBUTE" and symbol.get("symbol"):
                    changed_attributes.add(str(symbol["symbol"]))
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

        directly_affected = []
        possibly_affected = []
        unaffected = []

        for scenario in all_scenarios:
            key = (str(scenario.http_method or "").upper(), str(scenario.endpoint or ""))
            operation_baseline = operation_by_key.get(key)
            legacy = legacy_by_scenario.get(scenario.id)

            if operation_baseline:
                endpoint_flow = operation_baseline.endpoint_flow
                baseline_version = operation_baseline.baseline_version
                scenario_code = scenario.scenario_code
                http_method = operation_baseline.http_method
                endpoint = operation_baseline.endpoint
                declared_classes = set()
                baseline_scope = "OPERATION"
            elif legacy:
                endpoint_flow = legacy.endpoint_flow
                baseline_version = legacy.baseline_version
                scenario_code = legacy.scenario_code
                http_method = legacy.http_method
                endpoint = legacy.endpoint
                declared_classes = set(legacy.involved_classes or [])
                baseline_scope = "LEGACY_SCENARIO"
            else:
                # IMPORTANT: absence of a baseline is a coverage state, not proof
                # that the scenario is unaffected. Match the registered scenario
                # endpoint to the current controller mapping first.
                live_key = (
                    str(scenario.http_method or "").upper(),
                    self._canonical_endpoint(scenario.endpoint),
                )
                live_methods = live_controller_by_endpoint.get(live_key, set())
                matched_live_methods = sorted(live_methods & changed_method_keys)

                if matched_live_methods:
                    direct_methods = [
                        method for method in changed_methods
                        if f"{method['class_name']}.{method.get('method_name')}" in matched_live_methods
                    ]
                    directly_affected.append({
                        "scenario_id": scenario.id,
                        "scenario_code": scenario.scenario_code,
                        "http_method": scenario.http_method,
                        "endpoint": scenario.endpoint,
                        "baseline_version": None,
                        "baseline_scope": None,
                        "impact_status": "DIRECTLY_AFFECTED",
                        "matched_classes": sorted({
                            x.split(".", 1)[0] for x in matched_live_methods
                        }),
                        "declared_matched_classes": [],
                        "matched_methods": matched_live_methods,
                        "changed_methods": direct_methods,
                        "changed_attributes": sorted(changed_attributes),
                        "dependency_paths": [
                            {
                                "changed_symbol": method_key,
                                "path": [
                                    method_key,
                                    f"{str(scenario.http_method or '').upper()} {scenario.endpoint}",
                                ],
                            }
                            for method_key in matched_live_methods
                        ],
                        "test_baselines": [],
                        "jira_ids": [],
                        "reason": (
                            "Changed controller method maps to this registered endpoint; "
                            "no captured operation baseline exists yet."
                        ),
                    })
                else:
                    unaffected.append({
                        "scenario_id": scenario.id,
                        "scenario_code": scenario.scenario_code,
                        "http_method": scenario.http_method,
                        "endpoint": scenario.endpoint,
                        "baseline_version": None,
                        "baseline_scope": None,
                        "impact_status": "NO_BASELINE",
                        "matched_classes": [],
                        "changed_methods": [],
                        "dependency_paths": [],
                        "reason": "No active operation baseline has been captured for this scenario's HTTP operation.",
                    })
                continue

            flow_classes, flow_methods = self._extract_flow_dependencies(endpoint_flow)
            dependency_classes = declared_classes | flow_classes
            matched_classes = sorted(dependency_classes & changed_classes)
            declared_matched_classes = sorted(declared_classes & changed_classes)
            matched_method_keys = sorted(flow_methods & changed_method_keys)
            scenario_methods = [
                method for method in changed_methods
                if f"{method['class_name']}.{method.get('method_name')}" in matched_method_keys
            ]
            dependency_paths = self._build_dependency_paths(
                endpoint_flow=endpoint_flow,
                endpoint_label=f"{http_method} {endpoint}",
                changed_classes=changed_classes,
                changed_method_keys=changed_method_keys,
            )
            test_traceability = self._test_traceability(
                db=db,
                scenario_id=scenario.id,
                baseline_version=baseline_version,
                changed_attributes=changed_attributes,
                changed_classes=changed_classes,
            )
            base_result = {
                "scenario_id": scenario.id,
                "scenario_code": scenario_code,
                "baseline_version": baseline_version,
                "baseline_scope": baseline_scope,
                "http_method": http_method,
                "endpoint": endpoint,
                "matched_classes": matched_classes,
                "declared_matched_classes": declared_matched_classes,
                "matched_methods": matched_method_keys,
                "changed_methods": scenario_methods,
                "dependency_paths": dependency_paths,
                "changed_attributes": sorted(changed_attributes),
                "test_baselines": test_traceability,
                "jira_ids": sorted({
                    str(jira_id)
                    for test in test_traceability
                    for jira_id in (test.get("jira_ids") or [])
                    if jira_id
                }),
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
                    "reason": "Changed code intersects a method in the operation baseline execution flow.",
                })
            elif matched_classes:
                class_methods = [m for m in changed_methods if m["class_name"] in matched_classes]
                possibly_affected.append({
                    **base_result,
                    "impact_status": "POSSIBLY_AFFECTED",
                    "changed_methods": class_methods,
                    "reason": "Changed class is used by the operation baseline, but the exact changed method is not recorded in the flow.",
                })
            else:
                unaffected.append({
                    **base_result,
                    "impact_status": "UNAFFECTED",
                    "reason": "No changed class or method intersects this operation baseline.",
                })

        affected_scenarios = directly_affected + possibly_affected
        reverse_impact = self._build_reverse_impact(
            changed_symbols=changed_symbols,
            affected_scenarios=affected_scenarios,
        )
        shared_change_impact = [
            item for item in reverse_impact
            if item.get("endpoint_count", 0) > 1 or item.get("scenario_count", 0) > 1
        ]

        return {
            "status": "CHANGES_DETECTED" if changes.get("total_changed_java_files", 0) > 0 else "NO_CHANGES",
            "total_changed_java_files": changes.get("total_changed_java_files", 0),
            "changed_classes": sorted(changed_classes),
            "changed_methods": changed_methods,
            "changed_attributes": sorted(changed_attributes),
            "changed_symbols": changed_symbols,
            "reverse_impact": reverse_impact,
            "shared_change_impact": shared_change_impact,
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
            "baseline_model": "OPERATION_LEVEL",
        }

    def _test_traceability(
        self, db: Session, scenario_id: int, baseline_version: int | None,
        changed_attributes: set[str] | None = None,
        changed_classes: set[str] | None = None,
    ) -> list[dict]:
        """Keep test/JIRA evidence inside the changed business domain."""
        tests = (
            self.baselines.find_test_baselines(db, scenario_id)
            if baseline_version is None
            else self.baselines.find_test_baselines_for_code_version(
                db, scenario_id, int(baseline_version)
            )
        )
        if not tests:
            tests = self.baselines.find_test_baselines(db, scenario_id)

        jira_rows = db.query(JiraKnowledge).all()
        jira_map = {
            str(row.jira_id or "").strip().upper(): row
            for row in jira_rows if str(row.jira_id or "").strip()
        }
        attrs = {self._normalize_business_token(x) for x in (changed_attributes or set()) if x}
        domains = self._changed_domain_tokens(changed_classes or set())
        result = []

        for test in tests[:50]:
            name = str(test.baseline_name or "")
            jira_ids = list(test.jira_ids or [])
            jira_text = " ".join(
                " ".join([
                    str(getattr(jira_map.get(str(j).upper()), "title", "") or ""),
                    str(getattr(jira_map.get(str(j).upper()), "requirement", "") or ""),
                ]) for j in jira_ids
            )
            evidence = " ".join([
                name, str(getattr(test, "request_json", "") or ""),
                str(getattr(test, "expected_response_json", "") or ""),
                str(getattr(test, "actual_response_json", "") or ""),
                str(getattr(test, "expected_db_effect", "") or ""), jira_text,
            ])
            evidence_tokens = set(self._tokenize_business_text(evidence))
            test_tokens = set(self._tokenize_business_text(name))
            domain_match = bool(domains & test_tokens)
            attribute_match = bool(attrs & evidence_tokens)

            if domains and not domain_match:
                continue
            if attrs and not attribute_match and not domain_match:
                continue

            result.append({
                "id": test.id,
                "test_scenario": test.baseline_name,
                "status": test.status,
                "code_baseline_version": test.code_baseline_version,
                "jira_ids": jira_ids,
                "created_at": test.created_at.isoformat() if test.created_at else None,
            })
        return result

    @staticmethod
    def _normalize_business_token(value: str) -> str:
        value = str(value or "").strip()
        match = re.match(r"^(?:get|set|is)([A-Z].*)$", value)
        if match:
            value = match.group(1)
            value = value[:1].lower() + value[1:]
        return re.sub(r"[^a-z0-9]", "", value.lower())

    @staticmethod
    def _tokenize_business_text(value: str) -> list[str]:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
        text = text.replace("_", " ").replace("-", " ")
        return [token.lower() for token in re.findall(r"[A-Za-z0-9]+", text) if len(token) >= 2]

    @staticmethod
    def _changed_domain_tokens(changed_classes: set[str]) -> set[str]:
        suffixes = ("request","response","dto","entity","model","mapper","service","serviceimpl","controller","repository","impl")
        result = set()
        for class_name in changed_classes:
            stem = str(class_name or "")
            lowered = stem.lower()
            changed = True
            while changed:
                changed = False
                for suffix in suffixes:
                    if lowered.endswith(suffix) and len(stem) > len(suffix):
                        stem = stem[:-len(suffix)]
                        lowered = stem.lower()
                        changed = True
                        break
            parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", stem)
            if parts:
                result.add(parts[0].lower())
        return result

    @staticmethod
    def _canonical_endpoint(value: str) -> str:
        """Normalize endpoint text for deterministic registry/controller matching."""
        value = str(value or "").strip()
        value = re.sub(r"^https?://[^/]+", "", value, flags=re.I)
        value = value.split("?", 1)[0]
        value = re.sub(r"/+", "/", value)
        if not value.startswith("/"):
            value = "/" + value
        if len(value) > 1:
            value = value.rstrip("/")
        # Treat Spring variable names as equivalent: /{id} == /{studentId}
        value = re.sub(r"\{[^/{}]+\}", "{}", value)
        return value

    @staticmethod
    def _build_reverse_impact(
        changed_symbols: list[dict],
        affected_scenarios: list[dict],
    ) -> list[dict]:
        """Changed symbol -> endpoints -> scenarios -> test baselines/JIRAs."""
        result = []

        for symbol in changed_symbols:
            symbol_type = symbol.get("symbol_type")
            value = str(symbol.get("symbol") or "")
            class_name = str(symbol.get("class_name") or "")
            method_name = str(symbol.get("method_name") or "")
            matched = []

            for scenario in affected_scenarios:
                classes = set(scenario.get("matched_classes") or [])
                methods = set(scenario.get("matched_methods") or [])

                applies = False
                if symbol_type == "METHOD":
                    applies = value in methods
                elif symbol_type == "CLASS":
                    applies = value in classes or class_name in classes
                elif symbol_type == "ATTRIBUTE":
                    # Attribute change belongs to the changed class. Static flow
                    # intersection with that class proves the reverse-impact path.
                    applies = class_name in classes

                if applies:
                    matched.append(scenario)

            if not matched:
                continue

            endpoints = []
            scenarios = []
            jira_ids = set()
            tests = []
            seen_endpoints = set()
            seen_scenarios = set()
            seen_tests = set()

            for item in matched:
                endpoint_key = (
                    str(item.get("http_method") or "").upper(),
                    str(item.get("endpoint") or ""),
                )
                if endpoint_key not in seen_endpoints:
                    seen_endpoints.add(endpoint_key)
                    endpoints.append({
                        "http_method": endpoint_key[0],
                        "endpoint": endpoint_key[1],
                    })

                scenario_key = item.get("scenario_id") or item.get("scenario_code")
                if scenario_key not in seen_scenarios:
                    seen_scenarios.add(scenario_key)
                    scenarios.append({
                        "scenario_id": item.get("scenario_id"),
                        "scenario_code": item.get("scenario_code"),
                        "impact_status": item.get("impact_status"),
                    })

                for test in item.get("test_baselines") or []:
                    test_key = test.get("id") or (
                        item.get("scenario_id"),
                        test.get("test_scenario"),
                        test.get("code_baseline_version"),
                    )
                    if test_key not in seen_tests:
                        seen_tests.add(test_key)
                        tests.append(test)
                    jira_ids.update(str(x) for x in (test.get("jira_ids") or []) if x)

            result.append({
                **symbol,
                "endpoint_count": len(endpoints),
                "scenario_count": len(scenarios),
                "endpoints": endpoints,
                "scenarios": scenarios,
                "test_baselines": tests,
                "jira_ids": sorted(jira_ids),
                "shared_component": len(endpoints) > 1 or len(scenarios) > 1,
            })

        result.sort(key=lambda item: (
            -item.get("scenario_count", 0),
            -item.get("endpoint_count", 0),
            str(item.get("symbol") or ""),
        ))
        return result

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
