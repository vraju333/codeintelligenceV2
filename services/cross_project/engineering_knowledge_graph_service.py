from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline, ScenarioTestBaseline
from config import settings
from db_models import JiraKnowledge, Scenario
from services.project.project_registry_service import ProjectRegistryService


class EngineeringKnowledgeGraphService:
    """Phase 5 Neo4j relationship-intelligence layer.

    PostgreSQL/SQLite remains authoritative for scenarios, JIRAs and baselines.
    Neo4j stores identities plus relationships needed for traversal.
    """

    CLASS_RE = re.compile(r"\b(?:class|interface|record|enum)\s+([A-Za-z_]\w*)")
    METHOD_RE = re.compile(
        r"(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+"
        r"([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws\s+[^\{]+)?\{"
    )
    FIELD_RE = re.compile(r"(?:private|protected|public)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.?]+\s+([A-Za-z_]\w*)\s*(?:[;=])")
    MAPPING_RE = re.compile(
        r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"']",
        re.I,
    )
    GETTER_RE = re.compile(r"\.get([A-Z]\w*)\s*\(|\.is([A-Z]\w*)\s*\(")
    SETTER_RE = re.compile(r"\.set([A-Z]\w*)\s*\(")
    CALL_RE = re.compile(r"(?:\b([A-Za-z_]\w*)\s*\.)?([a-zA-Z_]\w*)\s*\(")

    def __init__(self):
        self.registry = ProjectRegistryService()

    @staticmethod
    def _key(*parts: Any) -> str:
        return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:24]

    @staticmethod
    def _camel(value: str) -> str:
        return value[:1].lower() + value[1:] if value else value

    @staticmethod
    def _method_end(source: str, brace_index: int) -> int:
        depth = 0
        for idx in range(brace_index, len(source)):
            if source[idx] == "{":
                depth += 1
            elif source[idx] == "}":
                depth -= 1
                if depth == 0:
                    return idx + 1
        return len(source)

    def _driver(self):
        if not settings.NEO4J_ENABLED:
            raise RuntimeError("Neo4j is disabled. Set NEO4J_ENABLED=true in .env.")
        return GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        )

    def status(self) -> dict:
        if not settings.NEO4J_ENABLED:
            return {"enabled": False, "status": "DISABLED", "uri": settings.NEO4J_URI}
        driver = None
        try:
            driver = self._driver()
            driver.verify_connectivity()
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                row = session.run(
                    "MATCH (n:EngineeringKnowledge) RETURN count(n) AS nodes"
                ).single()
                rel = session.run(
                    "MATCH (:EngineeringKnowledge)-[r]->(:EngineeringKnowledge) RETURN count(r) AS relationships"
                ).single()
            return {
                "enabled": True,
                "status": "CONNECTED",
                "uri": settings.NEO4J_URI,
                "database": settings.NEO4J_DATABASE,
                "nodes": int(row["nodes"] if row else 0),
                "relationships": int(rel["relationships"] if rel else 0),
            }
        except Exception as exc:
            return {"enabled": True, "status": "UNAVAILABLE", "error": str(exc)}
        finally:
            if driver:
                driver.close()

    def _projects(self, selected: list[str] | None) -> list[dict]:
        rows = self.registry.list_projects().get("projects", [])
        available = []
        for row in rows:
            root = Path(row.get("path") or "")
            if row.get("exists") and root.exists() and any(root.rglob("*.java")):
                available.append({"name": row.get("name") or root.name, "path": str(root.resolve())})
        if not selected:
            return available
        wanted = {x.strip() for x in selected if x and x.strip()}
        found = [p for p in available if p["name"] in wanted]
        missing = wanted - {p["name"] for p in found}
        if missing:
            raise ValueError("Unknown Java project(s): " + ", ".join(sorted(missing)))
        return found

    def _scan_project(self, project: dict) -> tuple[list[dict], list[dict]]:
        root = Path(project["path"])
        project_name = project["name"]
        nodes: dict[str, dict] = {}
        edges: set[tuple[str, str, str]] = set()

        def node(node_id: str, label: str, name: str, **props):
            nodes[node_id] = {"id": node_id, "label": label, "name": name, "project": project_name, **props}

        def edge(source: str, target: str, rel: str):
            edges.add((source, target, rel))

        pid = self._key("Project", project_name, root)
        node(pid, "Project", project_name, path=str(root))
        method_index: dict[str, list[str]] = {}
        typed_method_index: dict[tuple[str, str], str] = {}
        pending_calls: list[tuple[str, str, str | None, str, dict[str, str]]] = []

        for file in root.rglob("*.java"):
            source = file.read_text(encoding="utf-8", errors="ignore")
            cm = self.CLASS_RE.search(source)
            if not cm:
                continue
            class_name = cm.group(1)
            cid = self._key("Class", project_name, class_name)
            node(cid, "Class", class_name, file=str(file.resolve()))
            edge(pid, cid, "CONTAINS")

            # Receiver types from declarations and constructor-injected fields.
            # This is conservative source-level resolution, not full Java AST analysis.
            field_types = {name: typ for typ, name in re.findall(
                r"\b(?:private|protected|public)\s+(?:final\s+)?([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*[;=,)]", source)}
            fields = set(self.FIELD_RE.findall(source))
            for field in fields:
                aid = self._key("Attribute", project_name, class_name, field.lower())
                node(aid, "Attribute", f"{class_name}.{field}", attribute=field, owner=class_name)
                edge(cid, aid, "DECLARES")

            class_prefix = ""
            before_class = source[max(0, cm.start() - 800):cm.start()]
            class_maps = list(self.MAPPING_RE.finditer(before_class))
            if class_maps:
                class_prefix = class_maps[-1].group(2) or ""

            for mm in self.METHOD_RE.finditer(source):
                method_name = mm.group(1)
                brace = source.find("{", mm.end() - 1)
                body = source[mm.start():self._method_end(source, brace)] if brace >= 0 else ""
                mid = self._key("Method", project_name, class_name, method_name)
                node(mid, "Method", f"{class_name}.{method_name}", method=method_name, owner=class_name)
                edge(cid, mid, "DECLARES")
                method_index.setdefault(method_name, []).append(mid)
                typed_method_index[(class_name, method_name)] = mid

                for match in self.GETTER_RE.finditer(body):
                    attr = self._camel(match.group(1) or match.group(2))
                    aid = self._key("Attribute", project_name, class_name, attr.lower())
                    # owner may be another DTO/entity; generic attribute identity still enables impact search.
                    if aid not in nodes:
                        aid = self._key("Attribute", project_name, attr.lower())
                        node(aid, "Attribute", attr, attribute=attr)
                    edge(mid, aid, "READS")
                for match in self.SETTER_RE.finditer(body):
                    attr = self._camel(match.group(1))
                    aid = self._key("Attribute", project_name, attr.lower())
                    node(aid, "Attribute", attr, attribute=attr)
                    edge(mid, aid, "WRITES")

                for call in self.CALL_RE.finditer(body):
                    called = call.group(2)
                    if called not in {method_name, "if", "for", "while", "switch", "return", "new", "catch"}:
                        pending_calls.append((mid, called, call.group(1), class_name, field_types))

                prefix = source[max(0, mm.start() - 700):mm.start()]
                maps = list(self.MAPPING_RE.finditer(prefix))
                if maps:
                    mp = maps[-1]
                    http = mp.group(1).upper().replace("REQUEST", "ANY")
                    sub = mp.group(2) or ""
                    path = (class_prefix.rstrip("/") + "/" + sub.lstrip("/")) if class_prefix else sub
                    if not path.startswith("/"):
                        path = "/" + path
                    eid = self._key("Endpoint", project_name, http, path)
                    node(eid, "Endpoint", f"{http} {path}", http_method=http, path=path)
                    edge(eid, mid, "INVOKES")
                    edge(pid, eid, "EXPOSES")

        for source_id, called_name, receiver, owner, field_types in pending_calls:
            target = None
            if receiver in (None, "this"):
                target = typed_method_index.get((owner, called_name))
            elif receiver in field_types:
                target = typed_method_index.get((field_types[receiver], called_name))
            elif receiver:
                # Static calls such as CustomerJpaMapper.toEntity(...).
                target = typed_method_index.get((receiver, called_name))
            if target is None:
                targets = method_index.get(called_name, [])
                if len(targets) == 1:
                    target = targets[0]
            if target is not None and target != source_id:
                edge(source_id, target, "CALLS")

        return list(nodes.values()), [
            {"source": s, "target": t, "type": r} for s, t, r in sorted(edges)
        ]

    @staticmethod
    def _norm_path(value: str | None) -> str:
        if not value:
            return ""
        try:
            return str(Path(value).resolve()).lower()
        except Exception:
            return str(value).lower()

    @staticmethod
    def _norm_endpoint(value: str | None) -> str:
        value = str(value or "").strip() or "/"
        if not value.startswith("/"):
            value = "/" + value
        value = re.sub(r"/{2,}", "/", value)
        return value[:-1] if len(value) > 1 and value.endswith("/") else value

    def _business_nodes(
        self,
        db: Session,
        project_map: dict[str, str],
        selected_project_names: list[str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        nodes: dict[str, dict] = {}
        edges: set[tuple[str, str, str]] = set()

        def add(i, label, name, project=None, **props):
            nodes[i] = {"id": i, "label": label, "name": name, "project": project, **props}

        scenarios = db.query(Scenario).all()
        scenario_project: dict[int, str] = {}
        selected_project_names = [x for x in (selected_project_names or []) if x]
        single_selected_project = selected_project_names[0] if len(selected_project_names) == 1 else ""
        for s in scenarios:
            normalized_path = self._norm_path(s.project_path)
            project_name = project_map.get(normalized_path)

            # Older scenario rows were created before project_path became mandatory.
            # During a single-project graph sync they unambiguously belong to that
            # selected project, so keep them instead of silently dropping the
            # Endpoint -> Scenario bridge.  During an all/multi-project sync we do
            # NOT guess, because the same HTTP endpoint may exist in two projects.
            if not project_name and not s.project_path and single_selected_project:
                project_name = single_selected_project
            if project_map and not project_name:
                continue
            project_name = project_name or (Path(s.project_path).name if s.project_path else "")
            scenario_project[s.id] = project_name
            sid = self._key("Scenario", s.id)
            add(sid, "Scenario", s.scenario_code, project=project_name, project_path=s.project_path, db_id=s.id, description=s.scenario_name)
            http = str(s.http_method or "ANY").upper()
            endpoint = self._norm_endpoint(s.endpoint)
            # IMPORTANT: use the exact same Endpoint identity as static analysis.
            # This joins AST/code intelligence to PostgreSQL scenario intelligence.
            eid = self._key("Endpoint", project_name, http, endpoint)
            add(eid, "Endpoint", f"{http} {endpoint}", project=project_name, project_path=s.project_path,
                http_method=http, path=endpoint)
            edges.add((eid, sid, "COVERED_BY"))
            jira_ids = [x.strip().upper() for x in str(s.jira_id or "").replace(";", ",").split(",") if x.strip()]
            for jira in jira_ids:
                jid = self._key("Jira", jira)
                add(jid, "Jira", jira)
                edges.add((sid, jid, "CHANGED_BY"))

        # A JIRA's persisted project_path is explicit ownership evidence.
        # Only connect it to a registered workspace with an exact normalized path.
        # This does NOT assert that any Java method implements the JIRA.
        for j in db.query(JiraKnowledge).all():
            jira_key = str(j.jira_id or "").strip().upper()
            if not jira_key:
                continue
            jid = self._key("Jira", jira_key)
            workspace_name = project_map.get(self._norm_path(j.project_path))
            add(jid, "Jira", jira_key, project=workspace_name or j.project_path,
                project_path=j.project_path, title=j.title or "")
            if workspace_name:
                # Same identity as the project node produced by _scan_project.
                project_node = next((n for n in self._projects(None)
                                     if n["name"] == workspace_name and
                                     self._norm_path(n["path"]) == self._norm_path(j.project_path)), None)
                if project_node:
                    pid = self._key("Project", workspace_name, Path(project_node["path"]))
                    edges.add((jid, pid, "BELONGS_TO_PROJECT"))

        baselines = db.query(ScenarioBaseline).all()
        baseline_by_id = {b.id: b for b in baselines}
        for b in baselines:
            project_name = scenario_project.get(b.scenario_id, "")
            if project_map and not project_name:
                continue
            sid = self._key("Scenario", b.scenario_id)
            release_name = b.baseline_name or f"Baseline {b.baseline_version}"
            rid = self._key("Release", project_name, release_name)
            add(rid, "Release", release_name, project=project_name, release_version=b.release_version, code_baseline_version=b.baseline_version)
            edges.add((sid, rid, "RELEASED_IN"))

        tests = db.query(ScenarioTestBaseline).all()
        for t in tests:
            project_name = scenario_project.get(t.scenario_id, "")
            if project_map and not project_name:
                continue
            sid = self._key("Scenario", t.scenario_id)
            tid = self._key("TestBaseline", t.id)
            linked = baseline_by_id.get(t.baseline_id) if t.baseline_id else None
            release_name = (linked.baseline_name if linked and linked.baseline_name else t.baseline_name)
            release_version = (linked.release_version if linked and linked.release_version else t.code_baseline_version)
            version = f"V{release_version}" if release_version else f"Test-{t.id}"
            add(tid, "TestBaseline", version, project=project_name, db_id=t.id, baseline_name=t.baseline_name,
                release_name=release_name, release_version=release_version, status=t.status)
            edges.add((sid, tid, "TESTED_IN"))
            rid = self._key("Release", project_name, release_name)
            add(rid, "Release", release_name, project=project_name, release_version=release_version)
            edges.add((tid, rid, "BELONGS_TO"))
            for jira in (t.jira_ids or []):
                jira = str(jira).strip().upper()
                if jira:
                    jid = self._key("Jira", jira)
                    add(jid, "Jira", jira)
                    edges.add((tid, jid, "VERIFIES_CHANGE"))

        return list(nodes.values()), [{"source": s, "target": t, "type": r} for s, t, r in sorted(edges)]

    def ensure_jira_project_links(self, db: Session, project_path: str) -> dict:
        """Repair evidence-backed Jira ownership without deleting any graph data."""
        canon = lambda v: str(v or "").replace("\\", "/").rstrip("/").casefold()
        registered = next((p for p in self._projects(None)
                           if canon(p["path"]) == canon(project_path)), None)
        if registered is None:
            return {"status": "PROJECT_NOT_REGISTERED", "project_path": project_path}
        issues = [j for j in db.query(JiraKnowledge).all()
                  if canon(j.project_path) == canon(project_path) and j.jira_id]
        driver = self._driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                # Match an existing project by name, without creating duplicate project identities.
                count = session.run("MATCH (p:EngineeringKnowledge:Project {name:$name}) RETURN count(p) AS n",
                                    name=registered["name"]).single()["n"]
                if count != 1:
                    return {"status": "PROJECT_GRAPH_MISSING", "project_nodes": count}
                for issue in issues:
                    key = str(issue.jira_id).upper()
                    session.run("""
                        MATCH (p:EngineeringKnowledge:Project {name:$project_name})
                        MERGE (j:EngineeringKnowledge:Jira {id:$jira_id})
                        SET j.name=$jira_key, j.project=$project_name,
                            j.project_path=$project_path, j.title=$title
                        MERGE (j)-[:BELONGS_TO_PROJECT]->(p)
                    """, project_name=registered["name"], jira_id=self._key("Jira", key),
                        jira_key=key, project_path=project_path, title=issue.title or "").consume()
            return {"status": "SYNCED", "project": registered["name"], "jira_issues": len(issues)}
        finally:
            driver.close()

    def sync(self, db: Session, selected_projects: list[str] | None = None) -> dict:
        # Graph storage is globally replaced below. Always include all registered
        # projects; a selected-project rebuild would erase other projects.
        projects = self._projects(None)
        if not projects:
            raise ValueError("No registered Java project was found.")

        nodes: dict[str, dict] = {}
        edges: set[tuple[str, str, str]] = set()
        for project in projects:
            ns, es = self._scan_project(project)
            nodes.update({n["id"]: n for n in ns})
            edges.update((e["source"], e["target"], e["type"]) for e in es)

        project_map = {self._norm_path(p["path"]): p["name"] for p in projects}
        ns, es = self._business_nodes(
            db,
            project_map,
            selected_project_names=None,
        )
        nodes.update({n["id"]: n for n in ns})
        edges.update((e["source"], e["target"], e["type"]) for e in es)

        driver = self._driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                session.run("MATCH (n:EngineeringKnowledge) DETACH DELETE n")
                session.run("CREATE CONSTRAINT engineering_knowledge_id IF NOT EXISTS FOR (n:EngineeringKnowledge) REQUIRE n.id IS UNIQUE")
                for n in nodes.values():
                    label = re.sub(r"[^A-Za-z0-9_]", "", n["label"])
                    props = {k: v for k, v in n.items() if k not in {"label"} and v is not None}
                    session.run(
                        f"MERGE (n:EngineeringKnowledge:{label} {{id:$id}}) SET n += $props",
                        id=n["id"], props=props,
                    )
                for s, t, rel in edges:
                    rel = re.sub(r"[^A-Z_]", "", rel.upper())
                    session.run(
                        f"MATCH (a:EngineeringKnowledge {{id:$s}}),(b:EngineeringKnowledge {{id:$t}}) MERGE (a)-[:{rel}]->(b)",
                        s=s, t=t,
                    )
            return {
                "status": "SYNCED", "projects": [p["name"] for p in projects],
                "nodes": len(nodes), "relationships": len(edges), "neo4j": self.status(),
            }
        finally:
            driver.close()

    def impact(self, entity: str, project: str | None = None) -> dict:
        """Focused Phase-5 engineering traversal.

        Unlike generic search(), this deliberately follows only impact-bearing
        relationships and returns compact typed sections.
        """
        value = str(entity or "").strip()
        if not value:
            raise ValueError("entity is required")
        project = str(project or "").strip()
        driver = self._driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                seed_query = (
                    "MATCH (a:EngineeringKnowledge:Attribute) "
                    "WHERE (toLower(a.name)=toLower($q) OR toLower(coalesce(a.attribute,''))=toLower($q) "
                    "OR toLower(a.name)=toLower($qualified)) "
                    "AND ($project='' OR a.project=$project) "
                    "RETURN a ORDER BY CASE WHEN a.owner IS NULL THEN 1 ELSE 0 END, a.name LIMIT 20"
                )
                qualified = value if "." in value else "Student." + value
                seeds = [dict(r["a"]) for r in session.run(seed_query, q=value, qualified=qualified, project=project)]
                if not seeds:
                    return {"status":"NO_MATCH","entity":value,"project":project or None,"attributes":[],"methods":[],"endpoints":[],"scenarios":[],"jiras":[],"releases":[],"test_baselines":[],"paths":[]}
                seed_ids = [x["id"] for x in seeds]
                # Traverse in semantic direction only.  The previous undirected
                # traversal could walk Attribute <- READS - Mapper -> another
                # Attribute and then into unrelated Employee code.  Impact starts
                # at the attribute, walks backwards through callers to the HTTP
                # endpoint, then forward through scenario/JIRA/release/test data.
                rows = session.run(
                    "MATCH (a:EngineeringKnowledge:Attribute) WHERE a.id IN $ids "
                    "MATCH p=(a)<-[:READS|WRITES]-(m:EngineeringKnowledge:Method) "
                    "RETURN p "
                    "UNION "
                    "MATCH (a:EngineeringKnowledge:Attribute) WHERE a.id IN $ids "
                    "MATCH p=(a)<-[:READS|WRITES]-(m:EngineeringKnowledge:Method)<-[:CALLS*1..8]-(caller:EngineeringKnowledge:Method) "
                    "RETURN p "
                    "UNION "
                    "MATCH (a:EngineeringKnowledge:Attribute) WHERE a.id IN $ids "
                    "MATCH p=(a)<-[:READS|WRITES]-(m:EngineeringKnowledge:Method)<-[:INVOKES]-(e:EngineeringKnowledge:Endpoint) "
                    "RETURN p "
                    "UNION "
                    "MATCH (a:EngineeringKnowledge:Attribute) WHERE a.id IN $ids "
                    "MATCH p=(a)<-[:READS|WRITES]-(m:EngineeringKnowledge:Method)<-[:CALLS*0..8]-(caller:EngineeringKnowledge:Method)<-[:INVOKES]-(e:EngineeringKnowledge:Endpoint) "
                    "RETURN p "
                    "UNION "
                    "MATCH (a:EngineeringKnowledge:Attribute) WHERE a.id IN $ids "
                    "MATCH (a)<-[:READS|WRITES]-(m:EngineeringKnowledge:Method)<-[:CALLS*0..8]-(caller:EngineeringKnowledge:Method)<-[:INVOKES]-(e:EngineeringKnowledge:Endpoint) "
                    "MATCH p=(e)-[*1..5]->(n:EngineeringKnowledge) "
                    "WHERE all(r IN relationships(p) WHERE type(r) IN $business_relationships) "
                    "RETURN p",
                    ids=seed_ids,
                    business_relationships=[
                        "COVERED_BY", "CHANGED_BY", "RELEASED_IN", "TESTED_IN",
                        "BELONGS_TO", "VERIFIES_CHANGE",
                    ],
                )
                by_label: dict[str, dict[str, dict]] = {}
                paths = []
                for record in rows:
                    path = record["p"]
                    compact = []
                    for n in path.nodes:
                        props = dict(n)
                        labels = [x for x in n.labels if x != "EngineeringKnowledge"]
                        label = labels[0] if labels else "EngineeringKnowledge"
                        by_label.setdefault(label, {})[props.get("id")] = props
                        compact.append({"type": label, "name": props.get("name")})
                    if compact and compact not in paths:
                        paths.append(compact)
                def values(label: str):
                    return sorted(by_label.get(label, {}).values(), key=lambda x: str(x.get("name") or ""))
                return {
                    "status":"FOUND", "entity":value, "project":project or None,
                    "attributes": values("Attribute"),
                    "methods": values("Method"),
                    "endpoints": values("Endpoint"),
                    "scenarios": values("Scenario"),
                    "jiras": values("Jira"),
                    "releases": values("Release"),
                    "test_baselines": values("TestBaseline"),
                    "paths": paths[:100],
                    "counts": {k: len(values(k)) for k in ["Attribute","Method","Endpoint","Scenario","Jira","Release","TestBaseline"]},
                }
        finally:
            driver.close()

    def search(self, entity: str, max_hops: int = 8, limit: int = 250) -> dict:
        value = (entity or "").strip()
        if not value:
            raise ValueError("entity is required")
        max_hops = max(1, min(int(max_hops), 12))
        limit = max(1, min(int(limit), 1000))
        driver = self._driver()
        try:
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                seeds = [dict(r["n"]) for r in session.run(
                    "MATCH (n:EngineeringKnowledge) "
                    "WHERE toLower(n.name)=toLower($q) OR toLower(coalesce(n.attribute,''))=toLower($q) "
                    "OR toLower(coalesce(n.path,''))=toLower($q) "
                    "RETURN n LIMIT 25", q=value
                )]
                if not seeds:
                    seeds = [dict(r["n"]) for r in session.run(
                        "MATCH (n:EngineeringKnowledge) WHERE toLower(n.name) CONTAINS toLower($q) RETURN n LIMIT 25", q=value
                    )]
                ids = [n["id"] for n in seeds]
                if not ids:
                    return {"status": "NO_MATCH", "entity": value, "matches": [], "nodes": [], "relationships": []}
                query = (
                    f"MATCH p=(s:EngineeringKnowledge)-[*0..{max_hops}]-(n:EngineeringKnowledge) "
                    "WHERE s.id IN $ids UNWIND relationships(p) AS r "
                    "WITH collect(DISTINCT s)+collect(DISTINCT n) AS ns, collect(DISTINCT r) AS rs "
                    "UNWIND ns AS x WITH collect(DISTINCT x)[0..$limit] AS nodes, rs "
                    "RETURN [x IN nodes | properties(x)] AS nodes, "
                    "[r IN rs | {source:startNode(r).id,target:endNode(r).id,type:type(r)}][0..$limit] AS relationships"
                )
                row = session.run(query, ids=ids, limit=limit).single()
                return {
                    "status": "FOUND", "entity": value, "matches": seeds,
                    "nodes": row["nodes"] if row else seeds,
                    "relationships": row["relationships"] if row else [],
                }
        finally:
            driver.close()
