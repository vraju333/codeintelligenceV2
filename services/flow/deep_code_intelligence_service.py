from __future__ import annotations

import ast
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from config import settings


class DeepCodeIntelligenceService:
    """Phase 7 deterministic source intelligence.

    Builds lightweight control/data-flow evidence directly from current Java/Python
    source. No LLM is used for parsing. Results always retain file/method/line evidence.
    """

    JAVA_METHOD = re.compile(
        r"(?m)(?:public|protected|private|static|final|synchronized|abstract|native|\s)+"
        r"[A-Za-z_$][\w$<>,.?\[\] ]*\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{"
    )
    JAVA_IF = re.compile(r"\b(if|else\s+if|while)\s*\((.*?)\)", re.S)
    JAVA_FOR = re.compile(r"\bfor\s*\((.*?)\)", re.S)
    JAVA_ASSIGN = re.compile(r"\b(?:this\.)?([A-Za-z_$][\w$]*)\s*(=|\+=|-=|\*=|/=)\s*([^;]+);")
    JAVA_CALL = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
    IDENT = re.compile(r"\b[A-Za-z_$][\w$]*\b")
    STOP = {
        "if", "else", "while", "for", "return", "new", "true", "false", "null", "this",
        "public", "private", "protected", "static", "final", "void", "int", "long", "double",
        "float", "boolean", "string", "String", "class", "try", "catch", "throw", "throws",
    }

    def analyze(self, attribute: str | None = None, project_path: str | None = None) -> dict[str, Any]:
        root = self._root(project_path)
        java = list(root.rglob("*.java"))
        py = [p for p in root.rglob("*.py") if not any(x in p.parts for x in (".venv", "venv", "__pycache__"))]
        methods: list[dict[str, Any]] = []
        for path in java:
            methods.extend(self._java_methods(path, root))
        for path in py:
            methods.extend(self._python_methods(path, root))

        attr = self._norm(attribute) if attribute else None
        selected = methods
        if attr:
            selected = [m for m in methods if self._method_mentions(m, attr)]

        call_index = defaultdict(list)
        for method in methods:
            for call in method["calls"]:
                call_index[self._norm(call.split(".")[-1])].append(method)

        propagated = self._propagate(selected, methods, attr) if attr else []
        # Keep only branch/data-flow evidence that actually references the requested
        # attribute. A method can mention GPA and also contain unrelated branches
        # (course, yearOfStudy, etc.); those must not leak into a GPA answer.
        branches = [
            b for m in selected for b in m["branches"]
            if not attr or attr in set(b.get("attributes") or [])
        ]
        assignments = [
            a for m in selected for a in m["assignments"]
            if not attr
            or attr == self._norm(a.get("target"))
            or attr in set(a.get("source_attributes") or [])
        ]
        shared, shared_test_evidence = self._shared_component_impact(attr, methods) if attr else ([], [])

        return {
            "status": "FOUND" if selected else "NO_MATCH",
            "phase": 7,
            "project_path": str(root),
            "attribute": attribute,
            "languages": {"java_files": len(java), "python_files": len(py)},
            "summary": {
                "methods_direct": len(selected),
                "branches": len(branches),
                "assignments": len(assignments),
                "propagated_callers": len(propagated),
                "shared_components": len(shared),
            },
            "direct_methods": [self._public_method(m) for m in selected],
            "control_flow": branches,
            "data_flow": assignments,
            "data_lineage": self._data_lineage(attr, selected, propagated) if attr else [],
            "cross_method_flow": propagated,
            "shared_component_impact": shared,
            "shared_component_test_evidence": shared_test_evidence,
            "evidence_rule": "Phase 7 evidence is parsed deterministically from current source files; every branch/data-flow item retains file, method and line evidence.",
        }

    def control_flow(self, attribute: str, project_path: str | None = None) -> dict[str, Any]:
        result = self.analyze(attribute, project_path)
        return {k: result[k] for k in ("status", "phase", "project_path", "attribute", "control_flow", "cross_method_flow", "evidence_rule")}

    def data_flow(self, attribute: str, project_path: str | None = None) -> dict[str, Any]:
        result = self.analyze(attribute, project_path)
        return {k: result[k] for k in ("status", "phase", "project_path", "attribute", "data_flow", "data_lineage", "cross_method_flow", "evidence_rule")}

    def shared_impact(self, attribute: str, project_path: str | None = None) -> dict[str, Any]:
        result = self.analyze(attribute, project_path)
        return {k: result[k] for k in ("status", "phase", "project_path", "attribute", "shared_component_impact", "shared_component_test_evidence", "evidence_rule")}

    def _root(self, project_path: str | None) -> Path:
        raw = (project_path or settings.JAVA_PROJECT_PATH or "").strip()
        if not raw:
            raise ValueError("project_path is required or JAVA_PROJECT_PATH must be configured")
        root = Path(raw).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Project path does not exist: {raw}")
        return root

    @staticmethod
    def _norm(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _method_mentions(self, method: dict[str, Any], attr: str) -> bool:
        values = set(method.get("identifiers") or [])
        return attr in values or any(attr == self._norm(x.split(".")[-1]) for x in method.get("calls") or [])

    @staticmethod
    def _line(source: str, pos: int) -> int:
        return source.count("\n", 0, pos) + 1

    @staticmethod
    def _matching_brace(source: str, open_pos: int) -> int:
        depth = 0
        in_string = None
        escaped = False
        for i in range(open_pos, len(source)):
            c = source[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == in_string:
                    in_string = None
                continue
            if c in ('"', "'"):
                in_string = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return i
        return len(source) - 1


    @staticmethod
    def _balanced_paren_text(source: str, open_pos: int) -> tuple[str, int] | None:
        """Return text inside a balanced (...) pair and its closing position."""
        if open_pos < 0 or open_pos >= len(source) or source[open_pos] != "(":
            return None
        depth = 0
        in_string = None
        escaped = False
        for i in range(open_pos, len(source)):
            c = source[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == in_string:
                    in_string = None
                continue
            if c in ('"', "'"):
                in_string = c
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return source[open_pos + 1:i], i
        return None

    def _java_branches(self, body: str) -> list[dict[str, Any]]:
        """Extract Java IF/ELSE_IF/WHILE/FOR conditions with balanced parentheses."""
        results: list[dict[str, Any]] = []
        pattern = re.compile(r"\b(else\s+if|if|while|for)\s*\(", re.I)
        for match in pattern.finditer(body):
            open_pos = body.find("(", match.start(), match.end())
            parsed = self._balanced_paren_text(body, open_pos)
            if not parsed:
                continue
            condition, _ = parsed
            keyword = re.sub(r"\s+", "_", match.group(1).strip()).upper()
            results.append({"kind": keyword, "condition": condition, "pos": match.start()})
        return results

    def _java_methods(self, path: Path, root: Path) -> list[dict[str, Any]]:
        source = path.read_text(encoding="utf-8", errors="ignore")
        cls_match = re.search(r"\b(?:class|interface|record|enum)\s+([A-Za-z_$][\w$]*)", source)
        owner = cls_match.group(1) if cls_match else path.stem
        result = []
        for match in self.JAVA_METHOD.finditer(source):
            name = match.group(1)
            if name == owner:
                continue
            open_pos = source.find("{", match.start(), match.end())
            close_pos = self._matching_brace(source, open_pos)
            body = source[open_pos + 1:close_pos]
            body_offset = open_pos + 1
            identifiers = {self._norm(x) for x in self.IDENT.findall(body) if x not in self.STOP}
            calls = [c for c in self.JAVA_CALL.findall(body) if c.split(".")[-1] not in self.STOP]
            for call in calls:
                accessor = call.split(".")[-1]
                accessor_match = re.match(r"(?:get|set|is)([A-Z].*)$", accessor)
                if accessor_match:
                    identifiers.add(self._norm(accessor_match.group(1)))
            branches = []
            # Regex cannot correctly parse Java conditions containing nested calls
            # such as student.getGpa(). Scan balanced parentheses instead so the
            # complete condition is retained.
            for branch in self._java_branches(body):
                cond = " ".join(branch["condition"].split())
                branches.append(
                    self._branch(
                        path, root, owner, name, branch["kind"], cond, source,
                        body_offset + branch["pos"],
                    )
                )
            assignments = []
            for am in self.JAVA_ASSIGN.finditer(body):
                target, operator, expression = am.groups()
                assignments.append(self._assignment(path, root, owner, name, target, operator, expression, source, body_offset + am.start()))
            data_events = self._java_data_events(path, root, owner, name, body, source, body_offset)
            result.append({
                "language": "JAVA", "owner": owner, "method": name,
                "qualified_method": f"{owner}.{name}", "file": str(path.relative_to(root)),
                "line_start": self._line(source, match.start()), "line_end": self._line(source, close_pos),
                "identifiers": sorted(identifiers), "calls": sorted(set(calls)),
                "branches": branches, "assignments": assignments, "data_events": data_events,
            })
        return result

    def _branch(self, path, root, owner, method, kind, condition, source, pos):
        raw_ids = {self._norm(x) for x in self.IDENT.findall(condition) if x not in self.STOP}
        for accessor in re.findall(r"\b(?:get|set|is)([A-Z][A-Za-z0-9_$]*)\s*\(", condition):
            raw_ids.add(self._norm(accessor))
        ids = sorted(raw_ids)
        return {"language": "JAVA", "owner": owner, "method": method, "qualified_method": f"{owner}.{method}", "branch_type": kind, "condition": condition, "attributes": ids, "file": str(path.relative_to(root)), "line": self._line(source, pos)}

    def _assignment(self, path, root, owner, method, target, operator, expression, source, pos):
        sources = sorted({self._norm(x) for x in self.IDENT.findall(expression) if x not in self.STOP})
        return {"language": "JAVA", "owner": owner, "method": method, "qualified_method": f"{owner}.{method}", "target": target, "operator": operator, "expression": " ".join(expression.split()), "source_attributes": sources, "file": str(path.relative_to(root)), "line": self._line(source, pos)}

    def _java_data_events(self, path: Path, root: Path, owner: str, method: str, body: str, source: str, body_offset: int) -> list[dict[str, Any]]:
        """Capture attribute-bearing Java reads/writes that normal assignment regex misses.

        In particular, DTO/record accessors and entity setters such as
        s.setGpa(r.gpa()) are represented as an explicit READ -> WRITE event.
        """
        events: list[dict[str, Any]] = []
        # Keep statement-sized evidence. This is intentionally deterministic and
        # conservative: only accessor calls are promoted to data-flow evidence.
        for sm in re.finditer(r"[^;{}\n]+;", body):
            stmt = " ".join(sm.group(0).strip().rstrip(";").split())
            accessors = list(re.finditer(r"\b([A-Za-z_$][\w$]*)\.(get|set|is)([A-Z][A-Za-z0-9_$]*)\s*\(", stmt))
            record_reads = list(re.finditer(r"\b([A-Za-z_$][\w$]*)\.([a-z][A-Za-z0-9_$]*)\s*\(\s*\)", stmt))
            if not accessors and not record_reads:
                continue
            attrs = set()
            reads, writes = [], []
            for a in accessors:
                obj, kind, prop = a.groups()
                prop_n = self._norm(prop)
                attrs.add(prop_n)
                item = f"{obj}.{prop}"
                (writes if kind == "set" else reads).append(item)
            for r in record_reads:
                obj, prop = r.groups()
                # Avoid double counting getGpa()/setGpa() as record-style calls.
                if prop.startswith(("get", "set", "is")):
                    continue
                attrs.add(self._norm(prop))
                reads.append(f"{obj}.{prop}")
            events.append({
                "language": "JAVA", "owner": owner, "method": method,
                "qualified_method": f"{owner}.{method}", "statement": stmt,
                "attributes": sorted(attrs), "reads": sorted(set(reads)),
                "writes": sorted(set(writes)), "file": str(path.relative_to(root)),
                "line": self._line(source, body_offset + sm.start()),
                "scope": "TEST" if self._is_test_file(str(path.relative_to(root))) else "PRODUCTION",
            })
        return events

    @staticmethod
    def _is_test_file(file_name: str) -> bool:
        normalized = file_name.replace("\\", "/").lower()
        return "/src/test/" in f"/{normalized}" or normalized.startswith("src/test/") or "/tests/" in f"/{normalized}"

    def _data_lineage(self, attr: str, selected: list[dict[str, Any]], propagated: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return concise, attribute-sensitive production lineage evidence."""
        out: list[dict[str, Any]] = []
        seen = set()
        for m in selected:
            for event in m.get("data_events") or []:
                if attr not in set(event.get("attributes") or []):
                    continue
                if event.get("scope") != "PRODUCTION":
                    continue
                key = (event["qualified_method"], event["line"], event["statement"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "kind": "ATTRIBUTE_TRANSFER" if event.get("writes") and event.get("reads") else ("WRITE" if event.get("writes") else "READ"),
                    "method": event["qualified_method"], "statement": event["statement"],
                    "reads": event.get("reads") or [], "writes": event.get("writes") or [],
                    "file": event["file"], "line": event["line"], "scope": "PRODUCTION",
                })
        # Add only production caller edges that were resolved to an attribute-bearing callee.
        for edge in propagated:
            if edge.get("scope") != "PRODUCTION":
                continue
            key = (edge["from"], edge["calls"], edge["line"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": "METHOD_PROPAGATION", **edge})
        return sorted(out, key=lambda x: (x.get("file", ""), x.get("line", 0), x.get("kind", "")))

    def _python_methods(self, path: Path, root: Path) -> list[dict[str, Any]]:
        source = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        result = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parent = parents.get(node)
            owner = parent.name if isinstance(parent, ast.ClassDef) else path.stem
            identifiers = set()
            calls, branches, assignments = [], [], []
            for child in ast.walk(node):
                if isinstance(child, ast.Name): identifiers.add(self._norm(child.id))
                elif isinstance(child, ast.Attribute): identifiers.add(self._norm(child.attr))
                elif isinstance(child, ast.Call):
                    calls.append(self._py_name(child.func))
                elif isinstance(child, (ast.If, ast.While, ast.For)):
                    expr = child.test if hasattr(child, "test") else child.iter
                    condition = ast.unparse(expr)
                    branches.append({"language": "PYTHON", "owner": owner, "method": node.name, "qualified_method": f"{owner}.{node.name}", "branch_type": type(child).__name__.upper(), "condition": condition, "attributes": sorted({self._norm(x.id) for x in ast.walk(expr) if isinstance(x, ast.Name)} | {self._norm(x.attr) for x in ast.walk(expr) if isinstance(x, ast.Attribute)}), "file": str(path.relative_to(root)), "line": child.lineno})
                elif isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    target_node = child.targets[0] if isinstance(child, ast.Assign) and child.targets else getattr(child, "target", None)
                    value_node = getattr(child, "value", None)
                    if target_node is not None and value_node is not None:
                        assignments.append({"language": "PYTHON", "owner": owner, "method": node.name, "qualified_method": f"{owner}.{node.name}", "target": ast.unparse(target_node), "operator": type(child).__name__, "expression": ast.unparse(value_node), "source_attributes": sorted({self._norm(x.id) for x in ast.walk(value_node) if isinstance(x, ast.Name)} | {self._norm(x.attr) for x in ast.walk(value_node) if isinstance(x, ast.Attribute)}), "file": str(path.relative_to(root)), "line": child.lineno})
            result.append({"language": "PYTHON", "owner": owner, "method": node.name, "qualified_method": f"{owner}.{node.name}", "file": str(path.relative_to(root)), "line_start": node.lineno, "line_end": getattr(node, "end_lineno", node.lineno), "identifiers": sorted(identifiers), "calls": sorted(set(filter(None, calls))), "branches": branches, "assignments": assignments, "data_events": []})
        return result

    @staticmethod
    def _py_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute):
            left = DeepCodeIntelligenceService._py_name(node.value)
            return f"{left}.{node.attr}" if left else node.attr
        return ""

    @staticmethod
    def _public_method(method: dict[str, Any]) -> dict[str, Any]:
        return {k: method[k] for k in ("language", "owner", "method", "qualified_method", "file", "line_start", "line_end", "calls")}

    def _propagate(self, seeds: list[dict[str, Any]], methods: list[dict[str, Any]], attr: str | None = None) -> list[dict[str, Any]]:
        """Walk callers with class-aware call resolution.

        The old implementation matched only the method name (for example every
        `toResponse`), which connected Student GPA to unrelated Employee flows.
        Here a qualified call is matched to its likely owner and unqualified calls
        are limited to the same owner. This deliberately prefers precision.
        """
        by_owner_method = {(self._norm(m["owner"]), self._norm(m["method"])): m for m in methods}
        reverse: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def likely_owner(qualifier: str) -> str:
            q = qualifier.split(".")[-1]
            # studentMapper -> StudentMapper, studentService -> StudentService.
            return self._norm(q[:1].upper() + q[1:])

        for caller in methods:
            for call in caller.get("calls") or []:
                parts = call.split(".")
                method_name = self._norm(parts[-1])
                candidates: list[dict[str, Any]] = []
                if len(parts) > 1:
                    owner_guess = likely_owner(parts[-2])
                    exact = by_owner_method.get((owner_guess, method_name))
                    if exact:
                        candidates = [exact]
                    else:
                        # If qualifier is already a class name, normalized lookup works.
                        exact = by_owner_method.get((self._norm(parts[-2]), method_name))
                        if exact:
                            candidates = [exact]
                else:
                    same_owner = by_owner_method.get((self._norm(caller["owner"]), method_name))
                    if same_owner:
                        candidates = [same_owner]
                for callee in candidates:
                    reverse[callee["qualified_method"]].append(caller)

        seen = {m["qualified_method"] for m in seeds}
        queue = deque((m, 0) for m in seeds)
        output: list[dict[str, Any]] = []
        while queue:
            callee, depth = queue.popleft()
            if depth >= 4:
                continue
            for caller in reverse.get(callee["qualified_method"], []):
                key = caller["qualified_method"]
                if key in seen:
                    continue
                seen.add(key)
                scope = "TEST" if self._is_test_file(caller["file"]) else "PRODUCTION"
                output.append({
                    "from": caller["qualified_method"], "calls": callee["qualified_method"],
                    "depth": depth + 1, "file": caller["file"], "line": caller["line_start"],
                    "scope": scope, "attribute": attr,
                })
                queue.append((caller, depth + 1))
        # Production evidence first; tests remain available but are clearly separated.
        return sorted(output, key=lambda x: (x["scope"] != "PRODUCTION", x["depth"], x["from"]))

    def _shared_component_impact(self, attr: str, methods: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Separate production shared-component impact from test evidence."""
        production = defaultdict(list)
        tests = defaultdict(list)
        for method in methods:
            if not self._method_mentions(method, attr):
                continue
            (tests if self._is_test_file(method["file"]) else production)[method["owner"]].append(method)

        def component_type(owner: str) -> str:
            low = owner.lower()
            if "mapper" in low or "converter" in low or "adapter" in low: return "MAPPER"
            if "service" in low: return "SERVICE"
            if "controller" in low: return "CONTROLLER"
            if "repository" in low or "dao" in low: return "REPOSITORY"
            if any(x in low for x in ("util", "helper", "common", "shared")): return "HELPER_SHARED"
            return "ENTITY"

        result = []
        for owner, hits in production.items():
            ctype = component_type(owner)
            role_shared = ctype in {"MAPPER", "HELPER_SHARED"}
            if not role_shared and len(hits) <= 1:
                continue
            result.append({
                "component": owner, "component_type": ctype,
                "methods": sorted({m["qualified_method"] for m in hits}),
                "usage_count": len(hits), "files": sorted({m["file"] for m in hits}),
                "scope": "PRODUCTION",
                "reason": "shared_component_role" if role_shared else "attribute_reused_across_methods",
            })

        test_evidence = [{
            "component": owner, "component_type": "TEST",
            "methods": sorted({m["qualified_method"] for m in hits}),
            "usage_count": len(hits), "files": sorted({m["file"] for m in hits}),
            "scope": "TEST", "reason": "test_evidence_only",
        } for owner, hits in tests.items()]
        result.sort(key=lambda x: (x["component_type"] != "MAPPER", -x["usage_count"], x["component"]))
        test_evidence.sort(key=lambda x: (-x["usage_count"], x["component"]))
        return result, test_evidence
