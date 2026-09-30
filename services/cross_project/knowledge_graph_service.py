from __future__ import annotations

from sqlalchemy.orm import Session
from pathlib import Path
import re

from services.lineage.attribute_impact_service import AttributeImpactService
from services.cross_project.cross_project_graph_service import CrossProjectGraphService


class KnowledgeGraphService:
    """Single composition layer for Attribute Impact + Cross-Project Intelligence.

    Existing analyzers remain the evidence producers. CrossProjectGraphService owns
    the graph model and Neo4j mirror. This service merges both results into the
    response consumed by the single Attribute Analysis UI.
    """

    def __init__(self, cross_project: CrossProjectGraphService):
        self.attribute_impact = AttributeImpactService()
        self.cross_project = cross_project

    def analyze_attribute(self, attribute: str, db: Session) -> dict:
        attribute = (attribute or "").strip()
        if not attribute:
            raise ValueError("attribute is required")

        local = self.attribute_impact.analyze(attribute, db)
        cross = self.cross_project.impact(attribute)

        impacted = cross.get("impacted") or []
        relationships = cross.get("relationships") or []
        projects = sorted({
            (n.get("project") or n.get("name")) for n in impacted
            if n.get("project") or n.get("type") == "PROJECT"
        })
        cross_links = [r for r in relationships if r.get("type") == "DEPENDS_ON_PROJECT"]

        # Merge deterministic graph evidence into the same Attribute Impact
        # result.  This prevents the UI from saying "0 occurrences" while the
        # cross-project graph has exact attribute matches in other projects.
        local = self._merge_graph_evidence(local, cross, attribute)
        local = self._merge_project_endpoint_evidence(local, cross, attribute)
        local = self._merge_scenario_history(local, attribute, db)

        # Preserve explicit baseline -> JIRA relationships after endpoint/scenario
        # enrichment. This keeps GPA JIRAs visible while retaining the
        # primaryEmail endpoint-resolution fix.
        local = self._merge_historical_jiras(local)

        return {
            "status": "FOUND" if (local.get("total_occurrences") or cross.get("matches")) else "NO_EVIDENCE",
            "attribute": attribute,
            "attribute_impact": local,
            "knowledge_graph": {
                "status": cross.get("status"),
                "summary": cross.get("summary"),
                "selected_projects": cross.get("selected_projects") or [],
                "projects": projects,
                "project_count": len(projects),
                "direct_matches": cross.get("matches") or [],
                "impacted_nodes": impacted,
                "relationships": relationships,
                "cross_project_relationships": cross_links,
                "cross_project_relationship_count": len(cross_links),
            },
        }

    def _merge_project_endpoint_evidence(self, local: dict, cross: dict, attribute: str) -> dict:
        """Resolve controller endpoints from graph-matched methods without changing graph traversal.

        This is intentionally conservative: an endpoint is added only when its controller
        method directly calls one of the graph-matched method names (for example
        service.updateEmail(...)) or directly references the requested attribute.
        """
        result = dict(local or {})
        impacted = cross.get("impacted") or []
        relationships = cross.get("relationships") or []
        by_id = {n.get("id"): n for n in impacted}
        seed_ids = {n.get("id") for n in (cross.get("matches") or [])}

        matched_methods = []
        for rel in relationships:
            if rel.get("type") == "USES_ATTRIBUTE" and rel.get("target") in seed_ids:
                node = by_id.get(rel.get("source"))
                if node and node.get("type") == "METHOD":
                    matched_methods.append(node)
        if not matched_methods:
            return result

        method_names_by_project = {}
        for node in matched_methods:
            project = node.get("project")
            simple = str(node.get("name") or "").split(".")[-1]
            if project and simple:
                method_names_by_project.setdefault(project, set()).add(simple)

        registry = {p.get("name"): p for p in self.cross_project.available_projects().get("projects", [])}
        endpoints = list(result.get("affected_endpoints") or [])
        keys = {(str(e.get("http_method") or "").upper(), str(e.get("endpoint") or ""), str(e.get("project") or "")) for e in endpoints}
        attr_norm = re.sub(r"[^a-z0-9]", "", attribute.lower())

        mapping_re = re.compile(r'@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\']', re.I)
        method_re = re.compile(r'(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws\s+[^\{]+)?\{')

        for project, names in method_names_by_project.items():
            meta = registry.get(project)
            root = Path(meta.get("path")) if meta and meta.get("path") else None
            if not root or not root.exists():
                continue
            for file in root.rglob("*.java"):
                code = file.read_text(encoding="utf-8", errors="ignore")
                class_match = re.search(r'\b(?:class|interface|record)\s+([A-Za-z_]\w*)', code)
                class_name = class_match.group(1) if class_match else file.stem
                class_prefix = ""
                if class_match:
                    before_class = code[max(0, class_match.start()-800):class_match.start()]
                    cm = list(mapping_re.finditer(before_class))
                    if cm:
                        class_prefix = cm[-1].group(2) or ""
                for mm in method_re.finditer(code):
                    body = code[mm.start():self.cross_project._end(code, mm.end()-1)]
                    prefix = code[max(0, mm.start()-700):mm.start()]
                    maps = list(mapping_re.finditer(prefix))
                    if not maps:
                        continue
                    direct_call = any(re.search(r'\.\s*' + re.escape(name) + r'\s*\(', body) for name in names)
                    body_norm = re.sub(r"[^a-z0-9]", "", body.lower())
                    direct_attribute = bool(attr_norm and attr_norm in body_norm)
                    if not direct_call and not direct_attribute:
                        continue
                    mp = maps[-1]
                    http = mp.group(1).upper().replace("REQUEST", "ANY")
                    sub = mp.group(2) or ""
                    path = (class_prefix.rstrip("/") + "/" + sub.lstrip("/")) if class_prefix else sub
                    if not path.startswith("/"):
                        path = "/" + path
                    key = (http, path, project)
                    if key in keys:
                        continue
                    full_method = f"{class_name}.{mm.group(1)}"
                    endpoints.append({
                        "http_method": http, "endpoint": path, "controller": class_name,
                        "method_name": mm.group(1), "relevance": "GRAPH_CALL_RESOLVED",
                        "matched_methods": sorted({m.get("name") for m in matched_methods if m.get("project") == project}),
                        "matched_classes": sorted({str(m.get("name") or "").split(".")[0] for m in matched_methods if m.get("project") == project}),
                        "dependency_path": [f"{http} {path}", full_method, attribute],
                        "project": project,
                    })
                    keys.add(key)
        result["affected_endpoints"] = endpoints
        return result

    def _merge_scenario_history(self, local: dict, attribute: str, db: Session) -> dict:
        """Re-evaluate scenario/history after graph-derived endpoints are merged."""
        result = dict(local or {})
        endpoints = result.get("affected_endpoints") or []
        endpoint_keys = {(str(e.get("http_method") or "").upper(), str(e.get("endpoint") or "")) for e in endpoints}
        scenarios = list(result.get("affected_scenarios") or result.get("scenarios") or [])
        existing = {str(s.get("id")) for s in scenarios}
        for scenario in self.attribute_impact.scenarios.get_all(db):
            key = (str(scenario.http_method or "").upper(), str(scenario.endpoint or ""))
            if key not in endpoint_keys or str(scenario.id) in existing:
                continue
            history = self.attribute_impact._release_history_for_scenario(db, scenario.id, attribute_name=attribute, domain_tokens=set())
            scenarios.append({
                "id": scenario.id, "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name, "http_method": scenario.http_method,
                "endpoint": scenario.endpoint, "score": 4, "matched_classes": [],
                "reasons": ["Scenario uses a graph-resolved attribute endpoint"],
                "release_history": history,
            })
            existing.add(str(scenario.id))
        result["affected_scenarios"] = scenarios
        result["scenarios"] = scenarios
        return result

    def _merge_historical_jiras(self, local: dict) -> dict:
        result = dict(local or {})
        related = list(result.get("related_jiras") or [])
        by_id = {
            str(item.get("jira_id") or "").strip().upper(): item
            for item in related
            if str(item.get("jira_id") or "").strip()
        }

        for history in result.get("historical_traceability") or []:
            details = {
                str(item.get("jira_id") or "").strip().upper(): item
                for item in (history.get("jiras") or [])
                if str(item.get("jira_id") or "").strip()
            }
            for jira_id in history.get("jira_ids") or []:
                key = str(jira_id or "").strip().upper()
                if not key or key in by_id:
                    continue
                detail = details.get(key) or {}
                item = {
                    "jira_id": key,
                    "title": detail.get("title") or "",
                    "requirement": detail.get("requirement") or "",
                    "relationship": "LINKED_TEST_BASELINE",
                    "reasons": ["Linked through an attribute-grounded test baseline"],
                }
                related.append(item)
                by_id[key] = item

        result["related_jiras"] = related[:20]
        return result

    def _merge_graph_evidence(self, local: dict, cross: dict, attribute: str) -> dict:
        result = dict(local or {})
        impacted = cross.get("impacted") or []
        relationships = cross.get("relationships") or []
        by_id = {n.get("id"): n for n in impacted}
        seed_ids = {n.get("id") for n in (cross.get("matches") or [])}

        graph_methods = []
        for rel in relationships:
            if rel.get("type") != "USES_ATTRIBUTE" or rel.get("target") not in seed_ids:
                continue
            method = by_id.get(rel.get("source"))
            if method and method.get("type") == "METHOD":
                graph_methods.append(method)

        # Deduplicate methods and group them into a graph-backed code layer.
        seen = set()
        graph_methods = [m for m in graph_methods if not (m.get("id") in seen or seen.add(m.get("id")))]
        if graph_methods:
            layers = list(result.get("layers") or [])
            existing = {x for layer in layers for x in (layer.get("methods") or [])}
            additions = [m.get("name") for m in graph_methods if m.get("name") not in existing]
            if additions:
                layers.append({
                    "role": "KNOWLEDGE_GRAPH",
                    "classes": sorted({m.get("name", "").split(".", 1)[0] for m in graph_methods if m.get("name")}),
                    "methods": additions,
                })
            result["layers"] = layers

        # Graph endpoints are exact endpoint nodes connected to matched methods.
        endpoints = list(result.get("affected_endpoints") or [])
        endpoint_keys = {(str(e.get("http_method") or ""), str(e.get("endpoint") or "")) for e in endpoints}
        for rel in relationships:
            if rel.get("type") != "HANDLED_BY":
                continue
            ep = by_id.get(rel.get("source")); method = by_id.get(rel.get("target"))
            if not ep or not method or method not in graph_methods:
                continue
            props = ep.get("properties") or {}
            key = (str(props.get("http_method") or ""), str(props.get("path") or ""))
            if key in endpoint_keys:
                continue
            endpoints.append({
                "http_method": props.get("http_method") or "",
                "endpoint": props.get("path") or ep.get("name") or "",
                "controller": method.get("name", "").split(".", 1)[0],
                "method_name": method.get("name", "").split(".", 1)[-1],
                "relevance": "GRAPH_DIRECT",
                "matched_methods": [method.get("name")],
                "matched_classes": [method.get("name", "").split(".", 1)[0]],
                "dependency_path": [ep.get("name"), method.get("name"), attribute],
                "project": ep.get("project"),
            })
            endpoint_keys.add(key)
        result["affected_endpoints"] = endpoints

        local_count = int(result.get("total_occurrences") or 0)
        result["total_occurrences"] = max(local_count, len(graph_methods))
        result["graph_occurrences"] = len(graph_methods)
        result["graph_projects"] = sorted({m.get("project") for m in graph_methods if m.get("project")})

        confidence = dict(result.get("confidence") or {})
        if graph_methods and int(confidence.get("score") or 0) < 60:
            confidence = {"score": 60, "level": "MEDIUM"}
        result["confidence"] = confidence
        return result

