from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from services.flow.endpoint_flow_service import EndpointFlowService
from services.lineage.attribute_lineage_service import AttributeLineageService
from services.scenario.scenario_service import ScenarioService
from repositories.scenario_baseline_repository import ScenarioBaselineRepository
from db_models import JiraKnowledge


class AttributeImpactService:
    """Project-wide attribute blast-radius analysis.

    This composes the existing local attribute-lineage scanner with endpoint-flow
    and scenario metadata.  It does not call an LLM and it does not hard-code any
    application domain names.
    """

    def __init__(self):
        self.lineage = AttributeLineageService()
        self.endpoint_flow = EndpointFlowService()
        self.scenarios = ScenarioService()
        self.baselines = ScenarioBaselineRepository()

    def analyze(self, attribute_name: str, db: Session) -> dict:
        # Code analysis and JIRA RAG are independent branches and are orchestrated
        # in parallel by LangGraph, then merged into one Attribute Impact result.
        from graph.attribute_jira_graph import AttributeJiraGraph
        return AttributeJiraGraph(self._analyze_code_only).run(attribute_name, db)

    def _analyze_code_only(self, attribute_name: str, db: Session) -> dict:
        attribute_name = (attribute_name or "").strip()
        if not attribute_name:
            raise RuntimeError("attribute_name is required")

        lineage = self.lineage.analyze(attribute_name)
        occurrences = lineage.get("occurrences") or []
        impacted_classes = {
            item.get("class_name")
            for item in occurrences
            if item.get("class_name")
        }
        direct_methods = {
            f"{item.get('class_name')}.{item.get('method_name')}"
            for item in occurrences
            if item.get("class_name") and item.get("method_name")
        }

        endpoints = []
        for endpoint in self.endpoint_flow.discover_endpoints():
            http_method = str(endpoint.get("http_method") or "").upper()
            path = str(endpoint.get("endpoint") or "")
            try:
                flow = self.endpoint_flow.analyze_endpoint(http_method, path)
            except Exception:
                continue

            flow_methods = self._extract_methods(flow)
            flow_classes = {method.split(".", 1)[0] for method in flow_methods}
            matched_classes = sorted(flow_classes.intersection(impacted_classes))
            matched_methods = sorted(set(flow_methods).intersection(direct_methods))

            if not matched_classes and not matched_methods:
                continue

            score = len(matched_classes) * 3 + len(matched_methods) * 5

            # Initial classification. A second pass below separates methods that
            # are merely reused by many endpoint flows (for example a common
            # response mapper) from genuine direct/transitive attribute impact.
            controller_method = ".".join(
                part for part in (endpoint.get("class_name"), endpoint.get("method_name")) if part
            )
            if controller_method and controller_method in direct_methods:
                relevance = "DIRECT"
            elif matched_methods:
                relevance = "TRANSITIVE"
            else:
                relevance = "SHARED_FLOW"

            endpoints.append({
                "http_method": http_method,
                "endpoint": path,
                "controller": endpoint.get("class_name"),
                "method_name": endpoint.get("method_name"),
                "score": score,
                "relevance": relevance,
                "matched_classes": matched_classes,
                "matched_methods": matched_methods,
                "flow_methods": flow_methods,
                "branch_evidence": self._extract_attribute_branches(
                    flow=flow,
                    attribute_name=attribute_name,
                ),
                "dependency_path": self._build_path(
                    attribute_name=attribute_name,
                    flow_methods=flow_methods,
                    impacted_classes=impacted_classes,
                    direct_methods=direct_methods,
                    endpoint_label=f"{http_method} {path}",
                ),
            })

        # A direct attribute method that occurs in several unrelated endpoint
        # flows is normally a shared component (e.g. StudentMapper.toResponse),
        # not proof that every one of those endpoints directly changes the
        # attribute. Downgrade those paths to SHARED_FLOW while retaining the
        # evidence for regression analysis.
        method_usage = defaultdict(set)
        for item in endpoints:
            endpoint_key = (item["http_method"], item["endpoint"])
            for method in item.get("matched_methods") or []:
                method_usage[method].add(endpoint_key)

        for item in endpoints:
            if item["relevance"] != "TRANSITIVE":
                continue
            matched = item.get("matched_methods") or []
            if matched and all(len(method_usage[m]) > 1 for m in matched):
                item["relevance"] = "SHARED_FLOW"
                item["score"] = max(1, item["score"] - 4)

        relevance_rank = {"DIRECT": 0, "TRANSITIVE": 1, "SHARED_FLOW": 2}
        endpoints.sort(
            key=lambda item: (
                relevance_rank.get(item["relevance"], 9),
                -item["score"],
                item["endpoint"],
                item["http_method"],
            )
        )

        shared_component_impact = self._build_shared_component_impact(
            endpoints=endpoints,
            impacted_classes=impacted_classes,
            direct_methods=direct_methods,
        )

        endpoint_keys = {
            (item["http_method"], item["endpoint"])
            for item in endpoints
        }
        active_scenarios = self.scenarios.get_all(db)
        domain_tokens = self._infer_attribute_domain_tokens(occurrences, active_scenarios)

        scenarios = []
        for scenario in active_scenarios:
            key = (str(scenario.http_method).upper(), str(scenario.endpoint))
            involved = self._scenario_classes(scenario.involved_classes)

            if domain_tokens and not self._scenario_matches_domain(scenario, involved, domain_tokens):
                continue

            matched = sorted(involved.intersection(impacted_classes))
            endpoint_match = key in endpoint_keys
            endpoint_impact = next(
                (item for item in endpoints if (item["http_method"], item["endpoint"]) == key),
                None,
            )
            endpoint_relevance = (endpoint_impact or {}).get("relevance")
            text_match = self._scenario_text_matches(
                scenario, attribute_name, impacted_classes
            )

            # Evaluate historical children before deciding whether the operation
            # scenario is relevant. Operation-level scenarios such as UPDATE_DATA
            # may not repeat "gpa"; the concrete evidence can live in
            # STUDENT_UPDATE -> STUD-101 underneath that operation.
            release_history = self._release_history_for_scenario(
                db,
                scenario.id,
                attribute_name=attribute_name,
                domain_tokens=domain_tokens,
            )
            historical_match = any(
                version.get("tests")
                for release in release_history
                for version in (release.get("versions") or [])
            )

            # Endpoint-only is allowed only for code-impact scenarios when the
            # endpoint flow itself is attribute-affected. Historical traceability
            # remains independently grounded by test/JIRA evidence above.
            if not endpoint_match and not matched and not text_match and not historical_match:
                continue

            reasons = []
            score = 0
            if historical_match:
                score += 12
                reasons.append("A linked test baseline/JIRA contains attribute evidence")
            if matched:
                score += 8
                reasons.append("Scenario includes attribute-related classes: " + ", ".join(matched[:6]))
            if text_match:
                score += 6
                reasons.append("Scenario text mentions the attribute or related class")
            if endpoint_match:
                endpoint_points = {"DIRECT": 8, "TRANSITIVE": 5, "SHARED_FLOW": 2}.get(endpoint_relevance, 2)
                score += endpoint_points
                if endpoint_relevance == "DIRECT":
                    reasons.append("Scenario uses an endpoint that directly reads/writes/checks the attribute")
                elif endpoint_relevance == "TRANSITIVE":
                    reasons.append("Scenario reaches attribute logic transitively through the execution flow")
                else:
                    reasons.append("Scenario shares a component/response flow that contains attribute handling")

            # Code/static-analysis evidence owns the impact classification.
            # Historical JIRA/baseline evidence and scenario text can strengthen
            # traceability/confidence, but must not upgrade SHARED_FLOW to DIRECT.
            if endpoint_relevance in {"DIRECT", "TRANSITIVE", "SHARED_FLOW"}:
                scenario_relevance = endpoint_relevance
            elif matched:
                # Class-only evidence without a proven endpoint path is useful for
                # regression analysis, but is not enough to claim direct impact.
                scenario_relevance = "TRANSITIVE"
            else:
                # Historical/text-only evidence remains visible without claiming
                # that current source code directly depends on the attribute.
                scenario_relevance = "SHARED_FLOW"

            # Preserve the executable endpoint-flow evidence on the scenario.
            # git_scenario_impact() consumes affected_scenarios and later the SDLC
            # JUnit matcher needs these methods/paths to prove that a test actually
            # exercises the impacted production flow. Previously this information
            # was calculated above but dropped here, leaving static_junit_evidence
            # empty even when TestCodeAnalysisService found the correct tests.
            endpoint_matched_classes = list((endpoint_impact or {}).get("matched_classes") or [])
            scenario_matched_classes = sorted(set(matched).union(endpoint_matched_classes))
            endpoint_matched_methods = list((endpoint_impact or {}).get("matched_methods") or [])
            dependency_path = (endpoint_impact or {}).get("dependency_path") or []

            scenarios.append({
                "id": scenario.id,
                "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name,
                "http_method": scenario.http_method,
                "endpoint": scenario.endpoint,
                "score": score,
                "relevance": scenario_relevance,
                "matched_classes": scenario_matched_classes,
                "matched_methods": endpoint_matched_methods,
                "dependency_paths": [dependency_path] if dependency_path else [],
                "reasons": reasons,
                "release_history": release_history,
            })

        scenario_rank = {"DIRECT": 0, "TRANSITIVE": 1, "SHARED_FLOW": 2}
        scenarios.sort(key=lambda item: (
            scenario_rank.get(item.get("relevance"), 9),
            -item["score"],
            item["scenario_code"],
        ))

        self._attach_shared_component_scenarios(
            shared_component_impact=shared_component_impact,
            scenarios=scenarios,
        )

        role_groups = defaultdict(list)
        for item in occurrences:
            role_groups[item.get("class_role") or "JAVA_CLASS"].append(item)

        layer_summary = []
        preferred_order = [
            "REQUEST_MODEL", "DTO", "MODEL", "ENTITY", "MAPPER", "CONVERTER",
            "SERVICE", "CONTROLLER", "REPOSITORY", "JAVA_CLASS",
        ]
        seen = set()
        for role in preferred_order + sorted(role_groups):
            if role in seen or role not in role_groups:
                continue
            seen.add(role)
            classes = list(dict.fromkeys(
                item.get("class_name") for item in role_groups[role] if item.get("class_name")
            ))
            methods = list(dict.fromkeys(
                f"{item.get('class_name')}.{item.get('method_name')}"
                for item in role_groups[role]
                if item.get("class_name") and item.get("method_name")
            ))
            layer_summary.append({
                "role": role,
                "classes": classes,
                "methods": methods,
            })

        confidence_score = min(
            95,
            (35 if occurrences else 0)
            + (30 if endpoints else 0)
            + (20 if scenarios else 0)
            + min(10, len(direct_methods)),
        )
        confidence_level = (
            "HIGH" if confidence_score >= 75
            else "MEDIUM" if confidence_score >= 45
            else "LOW"
        )

        historical_traceability = []
        for scenario in scenarios:
            for release in scenario.get("release_history") or []:
                for version in release.get("versions") or []:
                    tests = version.get("tests") or []
                    if tests:
                        for test in tests:
                            historical_traceability.append({
                                "attribute": attribute_name,
                                "scenario_id": scenario.get("id"),
                                "scenario_code": scenario.get("scenario_code"),
                                "http_method": scenario.get("http_method"),
                                "endpoint": scenario.get("endpoint"),
                                "test_baseline": test.get("test_scenario"),
                                "test_status": test.get("status"),
                                "jira_ids": test.get("jira_ids") or [],
                                "jiras": test.get("jiras") or [],
                                "release": release.get("release"),
                                "release_version": version.get("release_version"),
                                "code_baseline_version": version.get("internal_version"),
                                "is_active": version.get("is_active"),
                                "created_at": test.get("created_at") or version.get("created_at"),
                            })
                    else:
                        historical_traceability.append({
                            "attribute": attribute_name,
                            "scenario_id": scenario.get("id"),
                            "scenario_code": scenario.get("scenario_code"),
                            "http_method": scenario.get("http_method"),
                            "endpoint": scenario.get("endpoint"),
                            "test_baseline": None,
                            "test_status": None,
                            "jira_ids": [],
                            "jiras": [],
                            "release": release.get("release"),
                            "release_version": version.get("release_version"),
                            "code_baseline_version": version.get("internal_version"),
                            "is_active": version.get("is_active"),
                            "created_at": version.get("created_at"),
                        })

        return {
            "status": "ANALYSIS_COMPLETE",
            "attribute": attribute_name,
            "total_occurrences": len(occurrences),
            "impacted_classes": sorted(impacted_classes),
            "direct_attribute_methods": sorted(direct_methods),
            "layers": layer_summary,
            "occurrences": occurrences,
            "affected_endpoints": endpoints[:20],
            "affected_scenarios": scenarios[:30],
            "shared_component_impact": shared_component_impact[:30],
            "historical_traceability": historical_traceability[:100],
            "domain_filter": {
                "mode": "DOMAIN_ONLY" if domain_tokens else "SHARED_OR_UNKNOWN",
                "tokens": sorted(domain_tokens),
            },
            "confidence": {
                "score": confidence_score,
                "level": confidence_level,
            },
            "analysis_basis": {
                "local_only": True,
                "llm_used": False,
                "source_code_sent_external": False,
            },
        }

    def _build_shared_component_impact(
        self,
        endpoints: list[dict],
        impacted_classes: set[str],
        direct_methods: set[str],
    ) -> list[dict]:
        """Find attribute-related classes/methods reused by multiple endpoints.

        A component is considered shared only when static endpoint flows prove that
        the same class or method participates in more than one distinct endpoint.
        """
        usage: dict[str, dict] = {}

        for endpoint in endpoints:
            endpoint_key = (
                str(endpoint.get("http_method") or "").upper(),
                str(endpoint.get("endpoint") or ""),
            )
            flow_methods = endpoint.get("flow_methods") or []

            for full_method in flow_methods:
                if "." not in full_method:
                    continue
                class_name, method_name = full_method.split(".", 1)

                candidates = []
                if full_method in direct_methods:
                    candidates.append(("METHOD", full_method, class_name, method_name))
                if class_name in impacted_classes:
                    candidates.append(("CLASS", class_name, class_name, None))

                for component_type, component, owner_class, owner_method in candidates:
                    key = f"{component_type}:{component}"
                    item = usage.setdefault(key, {
                        "component_type": component_type,
                        "component": component,
                        "class_name": owner_class,
                        "method_name": owner_method,
                        "endpoints": [],
                        "_endpoint_keys": set(),
                    })
                    if endpoint_key not in item["_endpoint_keys"]:
                        item["_endpoint_keys"].add(endpoint_key)
                        item["endpoints"].append({
                            "http_method": endpoint_key[0],
                            "endpoint": endpoint_key[1],
                            "controller": endpoint.get("controller"),
                            "method_name": endpoint.get("method_name"),
                            "relevance": endpoint.get("relevance"),
                        })

        result = []
        for item in usage.values():
            endpoint_count = len(item["_endpoint_keys"])
            if endpoint_count < 2:
                continue
            item.pop("_endpoint_keys", None)
            item["endpoint_count"] = endpoint_count
            item["shared"] = True
            item["scenarios"] = []
            result.append(item)

        result.sort(key=lambda item: (
            -item["endpoint_count"],
            0 if item["component_type"] == "METHOD" else 1,
            item["component"],
        ))
        return result

    @staticmethod
    def _attach_shared_component_scenarios(
        shared_component_impact: list[dict],
        scenarios: list[dict],
    ) -> None:
        """Attach already-filtered affected scenarios to each shared component."""
        for component in shared_component_impact:
            endpoint_keys = {
                (
                    str(item.get("http_method") or "").upper(),
                    str(item.get("endpoint") or ""),
                )
                for item in component.get("endpoints") or []
            }
            seen = set()
            linked = []
            for scenario in scenarios:
                key = (
                    str(scenario.get("http_method") or "").upper(),
                    str(scenario.get("endpoint") or ""),
                )
                if key not in endpoint_keys:
                    continue
                scenario_key = scenario.get("id") or scenario.get("scenario_code")
                if scenario_key in seen:
                    continue
                seen.add(scenario_key)
                linked.append({
                    "id": scenario.get("id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "scenario_name": scenario.get("scenario_name"),
                    "http_method": scenario.get("http_method"),
                    "endpoint": scenario.get("endpoint"),
                })
            component["scenarios"] = linked
            component["scenario_count"] = len(linked)

    def _release_history_for_scenario(
        self,
        db: Session,
        scenario_id: int,
        attribute_name: str = "",
        domain_tokens: set[str] | None = None,
    ) -> list[dict]:
        versions = self.baselines.find_all_for_scenario(db, scenario_id)
        if not versions:
            return []

        domain_tokens = {str(x).lower() for x in (domain_tokens or set()) if x}
        jira_rows = db.query(JiraKnowledge).all()
        jira_map = {
            str(row.jira_id or "").strip().upper(): row
            for row in jira_rows
            if str(row.jira_id or "").strip()
        }

        releases: dict[str, list[dict]] = {}
        for baseline in sorted(versions, key=lambda item: int(item.baseline_version or 0)):
            release_name = str(baseline.baseline_name or "Legacy").strip() or "Legacy"
            release_version = int(baseline.release_version or baseline.baseline_version or 1)
            tests = self.baselines.find_test_baselines_for_code_version(
                db, scenario_id, int(baseline.baseline_version)
            )

            relevant_tests = [
                test for test in tests
                if self._test_baseline_matches_attribute(
                    test=test,
                    attribute_name=attribute_name,
                    domain_tokens=domain_tokens,
                    jira_map=jira_map,
                )
            ]

            releases.setdefault(release_name, []).append({
                "baseline_id": baseline.id,
                "internal_version": int(baseline.baseline_version or 0),
                "release_version": release_version,
                "is_active": bool(baseline.is_active),
                "created_at": baseline.created_at.isoformat() if baseline.created_at else None,
                "tests": [
                    {
                        "id": test.id,
                        "test_scenario": test.baseline_name,
                        "status": test.status,
                        "jira_ids": list(test.jira_ids or []),
                        "jiras": [
                            {
                                "jira_id": jira_id,
                                "title": getattr(jira_map.get(str(jira_id).upper()), "title", None),
                                "requirement": getattr(jira_map.get(str(jira_id).upper()), "requirement", None),
                            }
                            for jira_id in (test.jira_ids or [])
                        ],
                        "created_at": test.created_at.isoformat() if test.created_at else None,
                    }
                    for test in relevant_tests
                ],
            })

        return [
            {"release": release_name, "versions": sorted(items, key=lambda item: item["release_version"])}
            for release_name, items in releases.items()
            if any(item.get("tests") for item in items)
        ]

    def _test_baseline_matches_attribute(
        self,
        test,
        attribute_name: str,
        domain_tokens: set[str],
        jira_map: dict,
    ) -> bool:
        """Ground test history in test data or an explicitly linked JIRA."""
        test_name = str(getattr(test, "baseline_name", "") or "")
        test_tokens = set(self._tokenize_name(test_name))

        # Preserve domain isolation for domain-owned attributes.
        if domain_tokens and not (domain_tokens & test_tokens):
            return False

        wanted = self._normalize_attribute(attribute_name)
        if not wanted:
            return False

        test_evidence = " ".join([
            test_name,
            str(getattr(test, "request_json", "") or ""),
            str(getattr(test, "expected_response_json", "") or ""),
            str(getattr(test, "actual_response_json", "") or ""),
            str(getattr(test, "expected_db_effect", "") or ""),
        ])
        if self._contains_normalized_attribute(test_evidence, wanted):
            return True

        # Crucial chain: operation scenario -> child test baseline -> linked JIRA.
        for jira_id in list(getattr(test, "jira_ids", None) or []):
            jira = jira_map.get(str(jira_id).strip().upper())
            if not jira:
                continue
            jira_evidence = " ".join([
                str(getattr(jira, "title", "") or ""),
                str(getattr(jira, "requirement", "") or ""),
            ])
            if self._contains_normalized_attribute(jira_evidence, wanted):
                return True

        return False

    def _contains_normalized_attribute(self, text: str, wanted: str) -> bool:
        raw = str(text or "")
        # Whole identifier comparison handles gpa/GPA and
        # residenceType/residence_type without substring matching.
        candidates = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw)
        return any(self._normalize_attribute(item) == wanted for item in candidates)

    @staticmethod
    def _tokenize_name(value: str) -> list[str]:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
        text = text.replace("_", " ").replace("-", " ")
        return [
            token.lower()
            for token in re.findall(r"[A-Za-z0-9]+", text)
            if len(token) >= 2
        ]

    def _scenario_text_matches(self, scenario, attribute_name: str, impacted_classes: set[str]) -> bool:
        haystack = " ".join(str(value or "") for value in (
            scenario.scenario_code,
            scenario.scenario_name,
            scenario.description,
            scenario.request_json,
            scenario.expected_response_json,
            scenario.expected_db_effect,
        )).lower()
        if not haystack:
            return False
        needles = {attribute_name.lower()}
        needles.update(item.lower() for item in impacted_classes if item)
        return any(needle and needle in haystack for needle in needles)

    def _extract_methods(self, flow: dict) -> list[str]:
        methods = []

        def add(class_name, method_name):
            if class_name and method_name:
                value = f"{class_name}.{method_name}"
                if value not in methods:
                    methods.append(value)

        def walk(value):
            if isinstance(value, dict):
                add(value.get("class_name"), value.get("method_name"))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str):
                match = re.match(
                    r"([A-Za-z_][A-Za-z0-9_]*)[.#:]([A-Za-z_][A-Za-z0-9_]*)",
                    value,
                )
                if match:
                    add(match.group(1), match.group(2))

        walk(flow.get("simplified_flow"))
        walk(flow.get("flow"))
        return methods

    def _extract_attribute_branches(self, flow: dict, attribute_name: str) -> list[dict]:
        wanted = self._normalize_attribute(attribute_name)
        evidence = []
        seen = set()

        def walk(value, owner_class=None, owner_method=None):
            if isinstance(value, dict):
                current_class = value.get("class_name") or owner_class
                current_method = value.get("method_name") or owner_method
                for branch in value.get("branches") or []:
                    attrs = branch.get("attributes") or []
                    if any(self._normalize_attribute(a) == wanted for a in attrs):
                        key = (
                            current_class, current_method,
                            branch.get("branch_type"), branch.get("condition")
                        )
                        if key not in seen:
                            seen.add(key)
                            evidence.append({
                                "class_name": current_class,
                                "method_name": current_method,
                                "branch_type": branch.get("branch_type"),
                                "condition": branch.get("condition"),
                                "attributes": attrs,
                                "calls": branch.get("calls") or [],
                            })
                for key, child in value.items():
                    if key != "branches":
                        walk(child, current_class, current_method)
            elif isinstance(value, list):
                for child in value:
                    walk(child, owner_class, owner_method)

        walk(flow.get("flow"))
        return evidence

    @staticmethod
    def _normalize_attribute(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _build_path(
        self,
        attribute_name: str,
        flow_methods: list[str],
        impacted_classes: set[str],
        direct_methods: set[str],
        endpoint_label: str,
    ) -> list[str]:
        relevant = [
            method for method in flow_methods
            if method in direct_methods or method.split(".", 1)[0] in impacted_classes
        ]
        if not relevant:
            relevant = flow_methods[:6]
        return list(dict.fromkeys([attribute_name, *relevant[:8], endpoint_label]))

    def _infer_attribute_domain_tokens(self, occurrences: list[dict], scenarios) -> set[str]:
        """Infer an exclusive business-domain owner without hard-coding domain names.

        We derive candidate owners from classes where the attribute is declared/used
        (Student, StudentRequest, StudentResponse -> student).  A candidate becomes a
        domain guard only when it also appears as the leading business token of an
        existing scenario code/name.  Shared value objects such as Address therefore
        remain unguarded and can legitimately affect multiple domains.
        """
        scenario_domain_tokens = set()
        for scenario in scenarios:
            code = str(getattr(scenario, "scenario_code", "") or "")
            name = str(getattr(scenario, "scenario_name", "") or "")
            for text in (code, name):
                parts = re.findall(r"[A-Za-z][A-Za-z0-9]*", text.replace("-", "_").replace(" ", "_"))
                if parts:
                    # Scenario codes are normally EMPLOYEE_ADD / STUDENT_UPDATE.
                    first = re.split(r"_+", text.strip())[0] if "_" in text else parts[0]
                    if first:
                        scenario_domain_tokens.add(first.lower())

        owner_tokens = set()
        suffixes = (
            "request", "response", "dto", "entity", "model", "mapper",
            "service", "serviceimpl", "controller", "repository", "impl",
        )
        for item in occurrences:
            class_name = str(item.get("class_name") or "")
            if not class_name:
                continue
            stem = class_name
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
            if stem:
                # Split CamelCase and use the leading business noun.
                parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", stem)
                if parts:
                    owner_tokens.add(parts[0].lower())

        return owner_tokens.intersection(scenario_domain_tokens)

    def _scenario_matches_domain(self, scenario, involved: set[str], domain_tokens: set[str]) -> bool:
        text = " ".join([
            str(getattr(scenario, "scenario_code", "") or ""),
            str(getattr(scenario, "scenario_name", "") or ""),
            str(getattr(scenario, "description", "") or ""),
            " ".join(sorted(involved)),
        ])
        tokens = {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9]*", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " "))}
        return bool(tokens.intersection(domain_tokens))

    def _scenario_classes(self, raw) -> set[str]:
        if not raw:
            return set()
        if isinstance(raw, str):
            values = re.split(r"[,;\n]", raw)
        else:
            values = list(raw)
        return {
            str(item).strip().split(".")[-1]
            for item in values
            if str(item).strip()
        }
