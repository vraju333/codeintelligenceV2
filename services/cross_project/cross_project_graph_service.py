from __future__ import annotations
import hashlib, json, re
from pathlib import Path
from config import settings
from services.project.project_registry_service import ProjectRegistryService

class CrossProjectGraphService:
    PACKAGE = re.compile(r"\bpackage\s+([\w.]+)\s*;")
    IMPORT = re.compile(r"\bimport\s+([\w.*]+)\s*;")
    CLASS = re.compile(r"\b(?:class|interface|record|enum)\s+([A-Za-z_]\w*)")
    METHOD = re.compile(r"(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws\s+[^{]+)?\{")
    ACCESS = re.compile(r"\.(?:get|set|is)([A-Z]\w*)\s*\(|\.([a-z]\w*)\s*\(\s*\)")
    ENDPOINT = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?[\"']([^\"']*)[\"']", re.I)

    def __init__(self):
        self.registry = ProjectRegistryService()
        self.nodes, self.edges = {}, []
        self.selected_project_names = []

    def available_projects(self):
        projects = []
        for p in self.registry.list_projects().get("projects", []):
            path = Path(p.get("path") or "")
            if not p.get("exists") or not path.exists():
                continue
            java_count = sum(1 for _ in path.rglob("*.java"))
            if java_count:
                projects.append({
                    "name": p.get("name") or path.name,
                    "path": str(path.resolve()),
                    "java_files": java_count,
                })
        return {"projects": sorted(projects, key=lambda x: x["name"].lower())}

    def sync(self, selected_projects: list[str]):
        selected = [x.strip() for x in (selected_projects or []) if x and x.strip()]
        if not selected:
            raise ValueError("Select at least one Java project.")

        available = self.available_projects()["projects"]
        by_name = {p["name"]: p for p in available}
        missing = [x for x in selected if x not in by_name]
        if missing:
            raise ValueError("Unknown or non-Java project(s): " + ", ".join(missing))

        self.nodes, self.edges = {}, []
        models = [self._scan(Path(by_name[name]["path"]), name) for name in selected]
        models = [m for m in models if m]
        if not models:
            raise ValueError("At least one Java project is required.")

        self.selected_project_names = [m["name"] for m in models]
        owners = [(pkg, m["id"]) for m in models for pkg in m["packages"]]
        for m in models:
            self._add(m)

        # Cross-project dependency is created only from deterministic import evidence.
        for m in models:
            for imp in m["imports"]:
                for pkg, owner in owners:
                    if owner != m["id"] and (imp == pkg or imp.startswith(pkg + ".")):
                        self._edge(m["id"], owner, "DEPENDS_ON_PROJECT", {"evidence": "import " + imp})

        neo4j = self._neo4j()
        cross_links = [e for e in self.edges if e["type"] == "DEPENDS_ON_PROJECT"]
        details = []
        for e in cross_links:
            source = self.nodes.get(e["source"], {})
            target = self.nodes.get(e["target"], {})
            details.append({
                "source_project": source.get("name") or source.get("project"),
                "target_project": target.get("name") or target.get("project"),
                "relationship": e["type"],
                "evidence": (e.get("properties") or {}).get("evidence"),
            })

        return {
            "status": "READY",
            "projects": len(models),
            "project_names": self.selected_project_names,
            "nodes": len(self.nodes),
            "relationships": len(self.edges),
            "cross_project_relationships": len(cross_links),
            "cross_project_details": details,
            "cross_project_status": (
                "ADD_ANOTHER_PROJECT" if len(models) == 1 else "READY"
            ),
            "neo4j": neo4j,
        }

    def graph(self):
        return {
            "selected_projects": self.selected_project_names,
            "nodes": list(self.nodes.values()),
            "relationships": self.edges,
        }

    def impact(self, query: str):
        """Return a focused, relationship-aware attribute subgraph.

        Attribute searches prefer exact ATTRIBUTE nodes.  The old generic
        three-hop traversal could reach a PROJECT node and then fan out to
        almost every class/method in that project, producing hundreds of
        loosely-related nodes.  This traversal keeps only the code path that
        actually touches the attribute plus project-to-project dependencies.
        """
        if not self.nodes:
            return {
                "status": "GRAPH_NOT_BUILT", "query": query, "matches": [],
                "impacted": [], "relationships": [],
                "summary": "Select Java projects and rebuild the graph first.",
            }

        q = (query or "").strip()
        qnorm = self._norm(q)
        if not qnorm:
            return {"status": "NO_EVIDENCE", "query": query, "matches": [],
                    "impacted": [], "relationships": [], "summary": "Empty query."}

        # Attribute analysis must be exact/case/format insensitive rather than
        # a broad substring search (primaryEmail == primary_email == Primary Email).
        seeds = [
            n for n in self.nodes.values()
            if n.get("type") == "ATTRIBUTE" and self._norm(n.get("name")) == qnorm
        ]
        if not seeds:
            seeds = [
                n for n in self.nodes.values()
                if qnorm in self._norm(n.get("name"))
            ]

        # A generic attribute name such as ``state``, ``status`` or ``code`` can
        # exist in unrelated domains.  Keep the strongest semantic family instead
        # of merging every same-named attribute into one cross-project concept.
        # Example: Address.state must not be joined to DealAgentGraphService.state.
        seeds = self._disambiguate_attribute_seeds(seeds)

        seed_ids = {n["id"] for n in seeds}
        method_ids = set()
        class_ids = set()
        endpoint_ids = set()
        project_ids = set()
        rels = []

        # ATTRIBUTE <-USES_ATTRIBUTE- METHOD
        for e in self.edges:
            if e["type"] == "USES_ATTRIBUTE" and e["target"] in seed_ids:
                method_ids.add(e["source"]); rels.append(e)

        # CLASS -HAS_METHOD-> METHOD
        for e in self.edges:
            if e["type"] == "HAS_METHOD" and e["target"] in method_ids:
                class_ids.add(e["source"]); rels.append(e)

        # ENDPOINT -HANDLED_BY-> METHOD (direct controller evidence)
        for e in self.edges:
            if e["type"] == "HANDLED_BY" and e["target"] in method_ids:
                endpoint_ids.add(e["source"]); rels.append(e)

        # Owning projects for the matched code only. Do not fan back out from
        # PROJECT through every HAS_CLASS/HAS_ENDPOINT edge.
        for nid in seed_ids | method_ids | class_ids | endpoint_ids:
            node = self.nodes.get(nid) or {}
            project = node.get("project")
            if project:
                for pnode in self.nodes.values():
                    if pnode.get("type") == "PROJECT" and pnode.get("name") == project:
                        project_ids.add(pnode["id"])
                        break

        # Include deterministic cross-project dependency edges touching an
        # attribute-owning project, plus the other project endpoint of the edge.
        dependency_project_ids = set()
        for e in self.edges:
            if e["type"] == "DEPENDS_ON_PROJECT" and (
                e["source"] in project_ids or e["target"] in project_ids
            ):
                rels.append(e)
                dependency_project_ids.update([e["source"], e["target"]])

        keep = seed_ids | method_ids | class_ids | endpoint_ids | project_ids | dependency_project_ids
        impacted = [self.nodes[i] for i in keep if i in self.nodes]
        projects = sorted({n.get("project") or n.get("name") for n in impacted
                           if n.get("project") or n.get("type") == "PROJECT"})
        return {
            "status": "FOUND" if seeds else "NO_EVIDENCE",
            "query": query,
            "selected_projects": self.selected_project_names,
            "matches": seeds,
            "impacted": impacted,
            "relationships": self._unique(rels),
            "summary": (
                f"{len(seeds)} direct attribute match(es); {len(method_ids)} method(s), "
                f"{len(endpoint_ids)} endpoint(s) across {len(projects)} project(s)."
            ),
        }

    def _disambiguate_attribute_seeds(self, seeds):
        """Separate unrelated domains that happen to use the same attribute name.

        We derive semantic tokens from the methods/classes that touch each exact
        ATTRIBUTE node, then keep the largest connected semantic family.  This is
        deliberately conservative and only activates when the same attribute is
        present in multiple projects and the evidence forms clearly separate
        families. Specific attributes such as gpa/primaryEmail therefore retain
        their existing behaviour.
        """
        if len(seeds) < 2:
            return seeds

        seed_ids = {n.get("id") for n in seeds}
        methods_by_seed = {sid: [] for sid in seed_ids}
        for edge in self.edges:
            if edge.get("type") == "USES_ATTRIBUTE" and edge.get("target") in seed_ids:
                method = self.nodes.get(edge.get("source")) or {}
                if method.get("type") == "METHOD":
                    methods_by_seed[edge.get("target")].append(method)

        stop = {
            "get", "set", "is", "to", "from", "apply", "create", "update",
            "delete", "read", "write", "response", "request", "service",
            "impl", "mapper", "controller", "details", "detail", "method",
        }

        def words(value):
            # Split camelCase/PascalCase as well as punctuation.
            text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
            return {
                w.lower() for w in re.findall(r"[A-Za-z0-9]+", text)
                if len(w) > 2 and w.lower() not in stop
            }

        evidence = {}
        for seed in seeds:
            sid = seed.get("id")
            tokens = set()
            for method in methods_by_seed.get(sid, []):
                tokens |= words(method.get("name"))
            # The attribute itself is identical for all seeds, so it must not be
            # used as a token when deciding whether two domains are related.
            tokens -= words(seed.get("name"))
            evidence[sid] = tokens

        # Build semantic components. Two seeds belong to the same concept when
        # their owning code shares at least one meaningful token (e.g. address).
        remaining = {n.get("id") for n in seeds}
        components = []
        while remaining:
            start = remaining.pop()
            comp = {start}
            changed = True
            while changed:
                changed = False
                comp_tokens = set().union(*(evidence.get(x, set()) for x in comp))
                for other in list(remaining):
                    if comp_tokens & evidence.get(other, set()):
                        remaining.remove(other)
                        comp.add(other)
                        changed = True
            components.append(comp)

        if len(components) <= 1:
            return seeds

        # Prefer the family represented across the most projects, then the one
        # with the most method evidence. A one-off workflow variable therefore
        # cannot pollute a business-domain attribute used across applications.
        seed_by_id = {n.get("id"): n for n in seeds}
        def rank(comp):
            projects = {seed_by_id[x].get("project") for x in comp if seed_by_id[x].get("project")}
            methods = sum(len(methods_by_seed.get(x, [])) for x in comp)
            return (len(projects), methods, len(comp))

        winner = max(components, key=rank)
        # Do not discard evidence on a tie: ambiguity is safer than an arbitrary
        # choice when two domains have equal support.
        top_rank = rank(winner)
        if sum(1 for c in components if rank(c) == top_rank) > 1:
            return seeds
        return [n for n in seeds if n.get("id") in winner]

    @staticmethod
    def _norm(value):
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _scan(self, root: Path, registered_name: str):
        java = list(root.rglob("*.java"))
        if not java:
            return None
        name = registered_name
        pid = self._id("PROJECT", str(root.resolve()))
        model = {
            "id": pid, "name": name, "path": str(root.resolve()),
            "packages": set(), "imports": set(), "classes": []
        }
        for f in java:
            s = f.read_text(encoding="utf-8", errors="ignore")
            p = self.PACKAGE.search(s)
            if p:
                model["packages"].add(p.group(1))
            model["imports"].update(self.IMPORT.findall(s))
            c = self.CLASS.search(s)
            if not c:
                continue
            methods = []
            for mm in self.METHOD.finditer(s):
                start = mm.start()
                body = s[start:self._end(s, mm.end() - 1)]
                attrs = sorted({
                    (a or b)[:1].lower() + (a or b)[1:]
                    for a, b in self.ACCESS.findall(body) if a or b
                })
                prefix = s[max(0, start - 500):mm.end()]
                eps = [{
                    "http_method": x.group(1).upper().replace("REQUEST", "ANY"),
                    "path": x.group(2)
                } for x in self.ENDPOINT.finditer(prefix)]
                methods.append({
                    "name": mm.group(1),
                    "attributes": attrs,
                    "endpoints": eps,
                })
            model["classes"].append({
                "name": c.group(1),
                "file": str(f.resolve()),
                "methods": methods,
            })
        model["packages"] = sorted(model["packages"])
        model["imports"] = sorted(model["imports"])
        return model

    def _add(self, m):
        self._node(m["id"], "PROJECT", m["name"], m["name"], {"path": m["path"]})
        for c in m["classes"]:
            cid = self._id("CLASS", m["name"], c["name"])
            self._node(cid, "CLASS", c["name"], m["name"], {"file": c["file"]})
            self._edge(m["id"], cid, "HAS_CLASS")
            for fn in c["methods"]:
                mid = self._id("METHOD", m["name"], c["name"], fn["name"])
                self._node(mid, "METHOD", c["name"] + "." + fn["name"], m["name"])
                self._edge(cid, mid, "HAS_METHOD")
                for a in fn["attributes"]:
                    aid = self._id("ATTRIBUTE", m["name"], a.lower())
                    self._node(aid, "ATTRIBUTE", a, m["name"])
                    self._edge(mid, aid, "USES_ATTRIBUTE")
                for ep in fn["endpoints"]:
                    eid = self._id("ENDPOINT", m["name"], ep["http_method"], ep["path"])
                    self._node(
                        eid, "ENDPOINT", ep["http_method"] + " " + ep["path"],
                        m["name"], ep
                    )
                    self._edge(m["id"], eid, "HAS_ENDPOINT")
                    self._edge(eid, mid, "HANDLED_BY")

    def _node(self, i, t, n, p=None, props=None):
        self.nodes[i] = {
            "id": i, "type": t, "name": n,
            "project": p, "properties": props or {}
        }

    def _edge(self, s, t, r, props=None):
        e = {"source": s, "target": t, "type": r, "properties": props or {}}
        if e not in self.edges:
            self.edges.append(e)

    @staticmethod
    def _id(*p):
        return hashlib.sha1("|".join(map(str, p)).encode()).hexdigest()[:20]

    @staticmethod
    def _end(s, i):
        d = 0
        for j in range(i, len(s)):
            if s[j] == "{":
                d += 1
            elif s[j] == "}":
                d -= 1
                if d == 0:
                    return j + 1
        return len(s)

    @staticmethod
    def _unique(xs):
        out, seen = [], set()
        for x in xs:
            k = (x["source"], x["target"], x["type"])
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out

    def _neo4j(self):
        if not getattr(settings, "NEO4J_ENABLED", False):
            return {"enabled": False, "status": "DISABLED"}
        try:
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD)
            )
            with driver.session(database=settings.NEO4J_DATABASE) as session:
                session.run("MATCH (n:CodeIntelligence) DETACH DELETE n")
                for n in self.nodes.values():
                    session.run(
                        "MERGE (n:CodeIntelligence {id:$id}) "
                        "SET n.type=$type,n.name=$name,n.project=$project,n.properties=$properties",
                        id=n["id"], type=n["type"], name=n["name"],
                        project=n.get("project"),
                        properties=json.dumps(n.get("properties") or {})
                    )
                for e in self.edges:
                    rel = re.sub(r"[^A-Z_]", "", e["type"].upper())
                    session.run(
                        f"MATCH (a:CodeIntelligence {{id:$s}}),(b:CodeIntelligence {{id:$t}}) "
                        f"MERGE (a)-[r:{rel}]->(b)",
                        s=e["source"], t=e["target"]
                    )
            driver.close()
            return {"enabled": True, "status": "SYNCED"}
        except Exception as exc:
            return {
                "enabled": True, "status": "UNAVAILABLE",
                "error": str(exc), "fallback": "LOCAL_GRAPH"
            }
