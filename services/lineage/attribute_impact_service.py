from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from services.flow.endpoint_flow_service import EndpointFlowService
from services.lineage.attribute_lineage_service import AttributeLineageService
from services.scenario.scenario_service import ScenarioService


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
            if matched_methods:
                relevance = "DIRECT"
            else:
                relevance = "CLASS_FLOW"

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
                "dependency_path": self._build_path(
                    attribute_name=attribute_name,
                    flow_methods=flow_methods,
                    impacted_classes=impacted_classes,
                    direct_methods=direct_methods,
                    endpoint_label=f"{http_method} {path}",
                ),
            })

        endpoints.sort(
            key=lambda item: (
                0 if item["relevance"] == "DIRECT" else 1,
                -item["score"],
                item["endpoint"],
                item["http_method"],
            )
        )

        endpoint_keys = {
            (item["http_method"], item["endpoint"])
            for item in endpoints
        }
        active_scenarios = self.scenarios.get_all_for_active_project(db)
        domain_tokens = self._infer_attribute_domain_tokens(occurrences, active_scenarios)

        scenarios = []
        for scenario in active_scenarios:
            key = (str(scenario.http_method).upper(), str(scenario.endpoint))
            involved = self._scenario_classes(scenario.involved_classes)

            # A shared endpoint such as /api/persons can execute both Student and
            # Employee branches.  Endpoint equality alone must therefore never
            # pull a sibling-domain scenario into a domain-owned attribute impact.
            # Example: Student.gpa must not report EMPLOYEE_ADD.
            if domain_tokens and not self._scenario_matches_domain(scenario, involved, domain_tokens):
                continue

            matched = sorted(involved.intersection(impacted_classes))
            endpoint_match = key in endpoint_keys
            if not endpoint_match and not matched:
                continue
            reasons = []
            score = 0
            if endpoint_match:
                score += 10
                reasons.append("Scenario uses an attribute-affected endpoint")
            if matched:
                score += 5
                reasons.append("Scenario includes attribute-related classes: " + ", ".join(matched[:6]))
            scenarios.append({
                "id": scenario.id,
                "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name,
                "http_method": scenario.http_method,
                "endpoint": scenario.endpoint,
                "score": score,
                "matched_classes": matched,
                "reasons": reasons,
            })

        scenarios.sort(key=lambda item: (-item["score"], item["scenario_code"]))

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
