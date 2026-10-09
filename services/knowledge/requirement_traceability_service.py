"""Non-destructive, project-scoped requirement-to-Java candidate discovery.

Explicit JIRA references are stored as REFERENCES_JIRA (not IMPLEMENTS).
Semantic matches are returned as candidates only, never asserted as verified.
"""
from __future__ import annotations

import re
import json
from collections import defaultdict, deque
from pathlib import Path
from sqlalchemy.orm import Session
from db_models import JiraKnowledge, MappingDefinition, Scenario
from baseline_models import ScenarioBaseline, ScenarioTestBaseline
from services.cross_project.engineering_knowledge_graph_service import EngineeringKnowledgeGraphService
from config import settings

_STOP = {"the", "and", "for", "from", "with", "into", "this", "that", "when", "then", "shall", "should", "must", "user", "system", "information", "data", "support", "provide", "using", "valid", "field"}


def _canon(value):
    return str(value or "").replace("\\", "/").rstrip("/").casefold()


def _tokens(value):
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    return {x.lower() for x in re.findall(r"[A-Za-z]{3,}", spaced) if x.lower() not in _STOP}


def _endpoint_key(method, endpoint):
    """Normalize trailing slashes, path variables and common API prefixes."""
    method = str(method or "").upper().strip()
    path = "/" + str(endpoint or "").split("?")[0].strip().strip("/")
    path = re.sub(r"/+", "/", path)
    path = re.sub(r"\{[^/{}]+\}", "{}", path)
    return method, path.casefold()


def _declared_classes(value):
    """Parse class declarations from JSON lists or delimited scenario text."""
    if not value:
        return set()
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        parsed = value
    if isinstance(parsed, dict):
        parsed = list(parsed.values())
    if not isinstance(parsed, (list, tuple, set)):
        parsed = re.split(r"[,;\s]+", str(parsed))
    return {str(x).rsplit(".", 1)[-1].casefold() for x in parsed if str(x).strip()}


def _scenario_candidates(scenarios, endpoint_methods, candidate_methods, call_edges, method_owners):
    """Infer potential coverage without promoting it to verified JIRA coverage."""
    neighbors = defaultdict(set)
    for caller, callee in call_edges:
        neighbors[caller].add(callee)
    results = []
    for sc in scenarios:
        method, path = _endpoint_key(sc.http_method, sc.endpoint)
        entries = set(endpoint_methods.get((method, path), set()))
        if not entries:
            # Different projects can use an API prefix in scenario registration.
            suffix_matches = [(key, methods) for key, methods in endpoint_methods.items()
                              if key[0] == method and key[1] != "/"
                              and (path.endswith(key[1]) or key[1].endswith(path))]
            if len(suffix_matches) == 1:
                entries.update(suffix_matches[0][1])
        reached = set(entries)
        frontier = [(m, 0) for m in entries]
        while frontier:
            caller, depth = frontier.pop(0)
            if depth >= 6:
                continue
            for callee in neighbors.get(caller, set()):
                if callee not in reached:
                    reached.add(callee)
                    frontier.append((callee, depth + 1))
        matching_methods = sorted(reached & candidate_methods)
        declared = _declared_classes(sc.involved_classes)
        matched_classes = sorted(declared & {method_owners[m].casefold() for m in candidate_methods
                                              if m in method_owners})
        if not matching_methods and not matched_classes:
            continue
        evidence = "ENDPOINT_CALL_PATH" if matching_methods else "DECLARED_CLASS_OVERLAP"
        results.append({"scenario_id": sc.id, "scenario_code": sc.scenario_code,
                        "scenario_name": sc.scenario_name, "endpoint": sc.endpoint,
                        "evidence": evidence, "classification": "INFERRED_SCENARIO_CANDIDATE",
                        "matched_methods": matching_methods, "matched_classes": matched_classes})
    return results


class RequirementTraceabilityService:
    def __init__(self):
        self.graph = EngineeringKnowledgeGraphService()

    def reconcile(self, db: Session, project_path: str) -> dict:
        registered = next((p for p in self.graph._projects(None)
                           if _canon(p["path"]) == _canon(project_path)), None)
        if registered is None:
            return {"status": "PROJECT_NOT_REGISTERED", "project_path": project_path}
        issues = [j for j in db.query(JiraKnowledge).all()
                  if j.jira_id and _canon(j.project_path) == _canon(project_path)]
        if not issues:
            return {"status": "NO_REQUIREMENTS", "project": registered["name"], "requirements": []}

        root = Path(registered["path"])
        method_re = self.graph.METHOD_RE
        # Collect methods once; use behavioral evidence and bounded call expansion.
        methods = {}
        for file in root.rglob("*.java"):
            if any(part in {"build", "target", ".gradle"} for part in file.parts):
                continue
            try:
                source = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            class_match = self.graph.CLASS_RE.search(source)
            if not class_match:
                continue
            owner = class_match.group(1)
            for match in method_re.finditer(source):
                name = match.group(1)
                brace = source.find("{", match.end() - 1)
                if brace < 0:
                    continue
                body = source[match.start():self.graph._method_end(source, brace)]
                prefix = source[max(0, match.start() - 400):match.start()]
                comments = re.findall(r"//[^\n]*|/\*[\s\S]*?\*/", prefix)
                key = owner + "." + name
                methods[key] = {"owner": owner, "name": name, "file": str(file),
                                "body": body, "comments": comments,
                                "calls": set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", body))}

        # Reuse the established engineering graph scanner for method-call evidence.
        # Its CALLS edges are only emitted for unique target method names.
        graph_nodes, graph_edges = self.graph._scan_project(registered)
        node_by_id = {node["id"]: node for node in graph_nodes}
        graph_methods = {node["name"]: node for node in graph_nodes if node["label"] == "Method"}
        calls_by_method = defaultdict(list)
        endpoint_to_methods = defaultdict(set)
        for edge in graph_edges:
            if edge["type"] == "INVOKES":
                endpoint = node_by_id.get(edge["source"])
                target = node_by_id.get(edge["target"])
                if endpoint and target and endpoint["label"] == "Endpoint":
                    endpoint_to_methods[_endpoint_key(endpoint.get("http_method"), endpoint.get("path"))].add(target["name"])
            if edge["type"] == "CALLS":
                source_node = node_by_id.get(edge["source"])
                target_node = node_by_id.get(edge["target"])
                if source_node and target_node:
                    calls_by_method[source_node["name"]].append(target_node["name"])
        mapping_rows = [row for row in db.query(MappingDefinition).all()
                        if _canon(row.project_path) == _canon(project_path)
                        and row.status == "ACTIVE"]
        results = []
        explicit_links = []
        behavior_groups = {
            "ingest": {"ingest", "receive", "request", "post", "create", "submit", "import"},
            "xml": {"xml", "parse", "deserialize", "marshal", "unmarshal"},
            "validate": {"validate", "validation", "reject", "invalid", "required", "check"},
            "persist": {"persist", "save", "repository", "entity", "database", "store"},
            "map": {"map", "mapper", "transform", "convert", "model", "entity"},
        }
        for issue in issues:
            jira_key = issue.jira_id.strip().upper()
            key_re = re.compile(r"(?<![A-Z0-9])" + re.escape(jira_key) + r"(?![A-Z0-9])", re.I)
            words = _tokens((issue.title or "") + " " + (issue.requirement or ""))
            requested = {k for k, synonyms in behavior_groups.items() if words & synonyms}
            # XML ingestion implies parsing, validation and persistence only when the
            # requirement itself describes these operations.
            candidates = []
            seed_keys = set()
            for key, item in methods.items():
                name = item["name"]
                terms = _tokens(item["owner"] + " " + name)
                overlap = sorted(words & terms)
                body_terms = _tokens(item["body"][:3000])
                direct = bool(key_re.search(item["body"]) or any(key_re.search(c) for c in item["comments"]))
                # Class names and large method bodies often contain unrelated domain
                # words; prioritize the method name and only use body as weak context.
                method_terms = _tokens(name)
                behaviors = sorted(k for k, synonyms in behavior_groups.items()
                                   if k in requested and (method_terms & synonyms))
                boilerplate = bool(re.match(r"^(get|set|is)[A-Z]", name)) or name == "main"
                unrelated = bool(re.search(r"^(delete|replace|update|remove)", name, re.I)
                                 and "update" not in words and "delete" not in words)
                if not direct and not overlap and not behaviors:
                    continue
                score = (0.22 * min(len(overlap), 4)
                         + 0.18 * len(behaviors)
                         + (0.35 if direct else 0)
                         - (0.65 if boilerplate else 0)
                         - (0.5 if unrelated else 0))
                if (boilerplate or unrelated) and not direct:
                    continue
                if not direct and not behaviors and not _tokens(name) & words:
                    continue
                if score < 0.15 and not direct:
                    continue
                if score >= 0.55 and not boilerplate and not unrelated:
                    seed_keys.add(key)
                candidates.append({
                    "method": key, "file": item["file"],
                    "evidence": "EXPLICIT_JIRA_REFERENCE" if direct else "BEHAVIOR_AND_TERM_MATCH",
                    "matched_terms": overlap, "matched_behaviors": behaviors,
                    "score": round(max(0, score), 3),
                    "classification": "REFERENCED" if direct else "CANDIDATE",
                    "call_depth": 0,
                })
                if direct:
                    explicit_links.append((jira_key, self.graph._key("Method", registered["name"],
                                                                    item["owner"], name), item["file"]))
            # Expand only graph scanner CALLS edges (unique name resolution).
            # The scanner does not provide full type-aware resolution, so these
            # are evidence-backed candidates, not verified implementations.
            seen = set(seed_keys)
            frontier = deque((key, 0) for key in seed_keys)
            while frontier:
                caller, depth = frontier.popleft()
                if depth >= 2:
                    continue
                for callee in calls_by_method.get(caller, []):
                    if callee in methods:
                        if callee in seen or callee == caller:
                            continue
                        seen.add(callee)
                        frontier.append((callee, depth + 1))
                        if any(c["method"] == callee for c in candidates):
                            continue
                        item = methods[callee]
                        if re.match(r"^(get|set|is)[A-Z]", item["name"]):
                            continue
                        candidates.append({
                            "method": callee, "file": item["file"],
                            "evidence": "GRAPH_CALLS_UNIQUE_NAME", "matched_terms": [],
                            "matched_behaviors": [], "score": round(0.35 / (depth + 1), 3),
                            "classification": "CANDIDATE", "call_depth": depth + 1,
                            "discovered_from": caller,
                        })
            candidates.sort(key=lambda c: (
                c["classification"] == "REFERENCED", c["score"], -c["call_depth"]), reverse=True)
            # A call graph is only useful if we expose the observed edges.
            # Unique-name resolution is still weaker than full Java type analysis.
            candidate_names = {c["method"] for c in candidates}
            observed_calls = [
                {"caller": caller, "callee": callee, "evidence": "SCANNER_UNIQUE_NAME_CALL"}
                for caller, callees in calls_by_method.items()
                for callee in callees
                if caller in candidate_names and callee in methods
            ]
            for candidate in candidates:
                candidate["observed_call_count"] = sum(
                    edge["caller"] == candidate["method"] for edge in observed_calls)
                if candidate["observed_call_count"]:
                    candidate["score"] = round(candidate["score"] + min(
                        candidate["observed_call_count"], 3) * 0.12, 3)
            candidates.sort(key=lambda c: (
                c["classification"] == "REFERENCED", c["score"], -c["call_depth"]), reverse=True)
            # Only link mapping rows whose target attribute appears in the
            # scanned project graph; preserve workbook row provenance.
            graph_attributes = [n for n in graph_nodes if n["label"] == "Attribute"]
            mapping_evidence = []
            for mapping in mapping_rows:
                target = str(mapping.target_attribute or "").strip().casefold()
                owner = str(mapping.target_class or "").strip().casefold()
                matching = [a for a in graph_attributes
                            if str(a.get("attribute") or "").casefold() == target
                            and (not owner or str(a.get("owner") or "").casefold() == owner)]
                if not matching:
                    continue
                mapping_evidence.append({
                    "mapping_id": mapping.id, "document_id": mapping.document_id,
                    "sheet": mapping.sheet_name, "row": mapping.row_number,
                    "source_type": mapping.source_type, "source_path": mapping.source_path,
                    "target_class": mapping.target_class,
                    "target_attribute": mapping.target_attribute,
                    "graph_attributes": [a["name"] for a in matching],
                    "evidence": "EXACT_ATTRIBUTE_MATCH",
                    "classification": "ATTRIBUTE_CANDIDATE",
                })
            # Use persisted project ownership, not the globally active project.
            # Explicit JIRA tags in either the scenario or its test baselines
            # establish coverage. Semantic similarity never establishes coverage.
            project_scenarios = [sc for sc in db.query(Scenario).all()
                                 if _canon(sc.project_path) == _canon(project_path)]
            scenario_items = []
            for sc in project_scenarios:
                direct_scenario = jira_key in {x.strip().upper() for x in
                    re.split(r"[,;\s]+", str(sc.jira_id or "")) if x.strip()}
                baselines = db.query(ScenarioTestBaseline).filter(
                    ScenarioTestBaseline.scenario_id == sc.id).all()
                matched_baseline = False
                for baseline in baselines:
                    baseline_jiras = {str(x).strip().upper() for x in (baseline.jira_ids or [])}
                    if not direct_scenario and jira_key not in baseline_jiras:
                        continue
                    matched_baseline = True
                    code_baseline = (db.query(ScenarioBaseline).filter(
                        ScenarioBaseline.id == baseline.baseline_id,
                        ScenarioBaseline.scenario_id == sc.id).first()
                        if baseline.baseline_id else None)
                    scenario_items.append({
                        "scenario_id": sc.id, "scenario_code": sc.scenario_code,
                        "scenario_name": sc.scenario_name, "endpoint": sc.endpoint,
                        "testing_baseline_id": baseline.id,
                        "testing_baseline_name": baseline.baseline_name,
                        "release_name": code_baseline.baseline_name if code_baseline else None,
                        "release_version": code_baseline.release_version if code_baseline else None,
                        "evidence": "EXPLICIT_JIRA_TEST_BASELINE" if jira_key in baseline_jiras
                                    else "EXPLICIT_JIRA_SCENARIO",
                    })
                if direct_scenario and not matched_baseline:
                    scenario_items.append({
                        "scenario_id": sc.id, "scenario_code": sc.scenario_code,
                        "scenario_name": sc.scenario_name, "endpoint": sc.endpoint,
                        "testing_baseline_id": None, "testing_baseline_name": None,
                        "release_name": None, "release_version": None,
                        "evidence": "EXPLICIT_JIRA_SCENARIO",
                    })
            # Scenario relevance is a separate, explicitly INFERRED signal.
            # Never turn endpoint/class similarity into verified JIRA coverage.
            scenario_candidates = _scenario_candidates(
                project_scenarios, endpoint_to_methods, candidate_names,
                [(caller, callee) for caller, callees in calls_by_method.items() for callee in callees],
                {name: item["owner"] for name, item in methods.items()})
            # Show historical baselines for inferred candidates, but never as
            # verified requirement coverage. Keep the original code baseline IDs.
            for candidate in scenario_candidates:
                historical = db.query(ScenarioTestBaseline).filter(
                    ScenarioTestBaseline.scenario_id == candidate["scenario_id"]).all()
                candidate["testing_baselines"] = []
                for baseline in historical:
                    code = (db.query(ScenarioBaseline).filter(
                        ScenarioBaseline.id == baseline.baseline_id,
                        ScenarioBaseline.scenario_id == candidate["scenario_id"]).first()
                        if baseline.baseline_id else None)
                    candidate["testing_baselines"].append({
                        "testing_baseline_id": baseline.id,
                        "testing_baseline_name": baseline.baseline_name,
                        "test_status": baseline.status,
                        "release_name": code.baseline_name if code else None,
                        "release_version": code.release_version if code else None,
                        "classification": "HISTORICAL_BASELINE_NOT_JIRA_VERIFIED"})
            scenario_evidence = {"status": "ANALYZED", "jira_id": jira_key,
                "project_scenarios_examined": len(project_scenarios),
                "covered_count": len(scenario_items),
                "scenario_count": len({i["scenario_id"] for i in scenario_items}),
                "items": scenario_items,
                "inferred_candidate_count": len(scenario_candidates),
                "inferred_candidates": scenario_candidates}
            results.append({"jira": jira_key, "candidates": candidates[:30],
                            "mapping_lineage": mapping_evidence,
                            "test_baseline_traceability": scenario_evidence,
                            "call_graph_evidence": "EXISTING_ENGINEERING_GRAPH_SCANNER",
                            "observed_call_edges": observed_calls,
                            "explicit_references": sum(c["classification"] == "REFERENCED" for c in candidates),
                            "implementation_status": "UNVERIFIED",
                            "analysis": "BEHAVIOR_GRAPH_MAPPING_BASELINE_INTEGRATION"})
        # Preserve all existing graph data. Link only methods already in this project graph.
        written = 0
        if explicit_links:
            driver = self.graph._driver()
            try:
                with driver.session(database=settings.NEO4J_DATABASE) as session:
                    for jira_key, method_id, file in explicit_links:
                        row = session.run("""
                            MATCH (j:EngineeringKnowledge:Jira {id:$jid})
                            MATCH (m:EngineeringKnowledge:Method {id:$mid, project:$project})
                            MERGE (m)-[r:REFERENCES_JIRA]->(j)
                            SET r.evidence='EXPLICIT_JIRA_REFERENCE', r.file=$file
                            RETURN count(r) AS n
                        """, jid=self.graph._key("Jira", jira_key), mid=method_id,
                            project=registered["name"], file=file).single()
                        written += int(row["n"] if row else 0)
            finally:
                driver.close()
        # Evidence-backed project-scoped graph facts. Never claim inferred
        # requirement-to-method candidates as verified implementations.
        graph_stats = {"mapping_attribute_links": 0, "jira_scenario_links": 0, "inferred_jira_scenario_links": 0}
        driver = self.graph._driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                for result in results:
                    for mapping in result.get("mapping_lineage", []):
                        for attribute in mapping["graph_attributes"]:
                            row = session.run("""
                                MATCH (a:EngineeringKnowledge:Attribute {project:$project, name:$name})
                                MERGE (m:EngineeringKnowledge:MappingEvidence
                                  {id:$id, project:$project})
                                SET m.document_id=$doc, m.sheet=$sheet, m.row=$row,
                                    m.source_path=$source, m.target_attribute=$target
                                MERGE (m)-[r:MAPS_TO_ATTRIBUTE]->(a)
                                SET r.evidence='EXACT_ATTRIBUTE_MATCH'
                                RETURN count(r) AS n
                            """, project=registered["name"], name=attribute,
                                id=f"mapping:{registered['name']}:{mapping['mapping_id']}",
                                doc=mapping["document_id"], sheet=mapping["sheet"],
                                row=mapping["row"], source=mapping["source_path"],
                                target=mapping["target_attribute"]).single()
                            graph_stats["mapping_attribute_links"] += int(row["n"] if row else 0)
                    for item in result["test_baseline_traceability"].get("inferred_candidates", []):
                        row = session.run("""
                            MATCH (j:EngineeringKnowledge:Jira {id:$jid})
                            MERGE (s:EngineeringKnowledge:ScenarioEvidence {id:$sid, project:$project})
                            SET s.scenario_code=$code, s.scenario_name=$name
                            MERGE (s)-[r:POSSIBLY_COVERS_JIRA]->(j)
                            SET r.evidence=$evidence, r.classification='INFERRED'
                            RETURN count(r) AS n
                        """, jid=self.graph._key("Jira", result["jira"]),
                            sid=f"scenario:{registered['name']}:{item['scenario_id']}",
                            project=registered["name"], code=item["scenario_code"],
                            name=item["scenario_name"], evidence=item["evidence"]).single()
                        graph_stats["inferred_jira_scenario_links"] += int(row["n"] if row else 0)
                    for item in result["test_baseline_traceability"]["items"]:
                        row = session.run("""
                            MATCH (j:EngineeringKnowledge:Jira {id:$jid})
                            MERGE (s:EngineeringKnowledge:ScenarioEvidence
                              {id:$sid, project:$project})
                            SET s.scenario_code=$code, s.scenario_name=$name
                            MERGE (s)-[r:COVERS_JIRA]->(j)
                            SET r.evidence=$evidence
                            RETURN count(r) AS n
                        """, jid=self.graph._key("Jira", result["jira"]),
                            sid=f"scenario:{registered['name']}:{item['scenario_id']}",
                            project=registered["name"], code=item["scenario_code"],
                            name=item["scenario_name"], evidence=item["evidence"]).single()
                        graph_stats["jira_scenario_links"] += int(row["n"] if row else 0)
        finally:
            driver.close()
        return {"status": "ANALYZED", "project": registered["name"],
                "requirements": results, "explicit_graph_references": written,
                "graph_traceability": graph_stats,
                "note": "Call expansion reuses unique-name graph edges and is not type-aware; candidates are not verified implementations. REFERENCES_JIRA only records explicit references."}
