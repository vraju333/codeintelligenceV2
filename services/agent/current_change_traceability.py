"""Evidence-bounded current Git -> attribute -> JIRA -> operations -> mapping report."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any
from services.jira.attribute_jira_lookup_service import lookup as lookup_attribute_jiras


def _attributes_from_changes(git: dict) -> list[dict]:
    """Only infer an attribute when changed source provides a concrete field reference."""
    results = {}
    for file in git.get("changed_files", []):
        if str(file.get("file_path", "")).replace("\\", "/").startswith("src/test/"):
            continue
        semantic = file.get("semantic_analysis") or {}
        method_names = {m.get("method_name") for m in file.get("changed_methods", [])}
        for change in semantic.get("constant_changes", []):
            name = str(change.get("symbol") or "")
            if not name:
                continue
            before, after = str(change.get("before") or ""), str(change.get("after") or "")
            # Require a real semantic constant change; do not infer arbitrary attributes from a class name.
            for attr in re.findall(r"get([A-Z][A-Za-z0-9_]*)\s*\(", _read_source(git, file)):
                attribute = attr[:1].lower() + attr[1:]
                # Source references are candidate links. Prefer a validation expression
                # that uses this constant and that exact getter in the same method.
                source = _read_source(git, file)
                if not re.search(r"\b" + re.escape(name) + r"\s*\.\s*matcher\s*\(\s*[^)]*get" + re.escape(attr) + r"\s*\(", source, re.S):
                    continue
                key = (attribute, name)
                results[key] = {
                    "attribute": attribute, "changed_constant": name,
                    "before": before, "after": after,
                    "changed_file": file.get("file_path"),
                    "functional_methods": sorted(method_names),
                    "evidence_classification": "SOURCE_CONSTANT_AND_GETTER_USAGE",
                }
    return list(results.values())


def _read_source(git: dict, file: dict) -> str:
    root = Path(str(git.get("git_root") or git.get("project_path") or ""))
    rel = str(file.get("file_path") or "")
    try:
        target = (root / rel).resolve()
        if not target.is_relative_to(root.resolve()) or target.suffix != ".java":
            return ""
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _callers(git: dict, changed_file: dict, method: str) -> list[dict]:
    """Direct same-class calls only; not an asserted controller-to-endpoint call graph."""
    source = _read_source(git, changed_file)
    # Approximate Java method body boundaries with brace depth; report evidence as source-level candidate.
    signature = re.compile(r"\b(?:public|private|protected)\s+(?:[\w<>?,\[\] ]+\s+)?([a-zA-Z_]\w*)\s*\([^;{}]*\)\s*\{", re.S)
    callers = []
    for match in signature.finditer(source):
        name = match.group(1)
        if name == method:
            continue
        start, depth, pos = match.end(), 1, match.end()
        while pos < len(source) and depth:
            depth += (source[pos] == "{") - (source[pos] == "}")
            pos += 1
        body = source[start:pos-1]
        if re.search(r"(?<![\w.])" + re.escape(method) + r"\s*\(", body):
            callers.append({"method": name, "calls": method, "evidence": "DIRECT_SAME_CLASS_SOURCE_CALL", "operation": "CANDIDATE_REQUIRES_CONTROLLER_ROUTE_VERIFICATION"})
    return callers


def _controller_operations(git: dict, service_method: str) -> list[dict]:
    """Resolve Spring routes only when a controller calls the exact service method."""
    root = Path(str(git.get("git_root") or git.get("project_path") or ""))
    if not root.is_dir() or not re.fullmatch(r"[A-Za-z_]\w*", service_method):
        return []
    operations = []
    annotations = {"PostMapping": "POST", "PutMapping": "PUT", "PatchMapping": "PATCH",
                   "GetMapping": "GET", "DeleteMapping": "DELETE", "RequestMapping": "REQUEST"}
    for path in root.glob("src/main/java/**/*Controller.java"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        base_match = re.search(r'@RequestMapping\s*\(\s*(?:value\s*=\s*)?"([^"]+)"', source)
        base = base_match.group(1) if base_match else ""
        # Controller method declarations may have multiple annotations and generic return types.
        pattern = re.compile(r'@(?P<verb>PostMapping|PutMapping|PatchMapping|GetMapping|DeleteMapping)\s*(?:\((?P<args>[^)]*)\))?\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*public\s+[\w<>?, ]+\s+(?P<name>\w+)\s*\([^)]*\)\s*\{', re.S)
        for match in pattern.finditer(source):
            start, pos, depth = match.end(), match.end(), 1
            while pos < len(source) and depth:
                depth += (source[pos] == "{") - (source[pos] == "}")
                pos += 1
            body = source[start:pos-1]
            if not re.search(r'\b\w+\s*\.\s*' + re.escape(service_method) + r'\s*\(', body):
                continue
            args = match.group('args') or ''
            route_match = re.search(r'(?:value|path)\s*=\s*"([^"]+)"|"([^"]+)"', args)
            suffix = (route_match.group(1) or route_match.group(2)) if route_match else ''
            endpoint = (base.rstrip('/') + '/' + suffix.lstrip('/')).rstrip('/') or '/'
            kind = 'SHARED_VALIDATION_REGRESSION_DEPENDENCY' if service_method == 'updateAccountType' else 'DIRECT_VALIDATION_IMPACT'
            operations.append({'http_method': annotations[match.group('verb')], 'endpoint': endpoint,
                               'controller_method': path.stem + '.' + match.group('name'),
                               'service_method': service_method, 'classification': kind,
                               'evidence': 'CONTROLLER_ANNOTATION_AND_SERVICE_METHOD_CALL',
                               'source_file': str(path.relative_to(root)).replace('\\', '/')})
    return operations


def build(db: Any, git: dict, mapping_service: Any) -> dict:
    attribute_changes = _attributes_from_changes(git)
    result = {
        "status": "FOUND" if attribute_changes else "NO_ATTRIBUTE_RULE_CHANGE_IDENTIFIED",
        "analysis_basis": "Current HEAD-to-working-tree semantic Git changes, live source call references, indexed JIRA and persisted mapping lineage",
        "git_evidence": {
            "git_root": git.get("git_root"),
            "changed_methods": git.get("changed_methods", []),
            "constant_value_changes": git.get("constant_value_changes", []),
            "changed_files": [{"file_path": f.get("file_path"), "semantic_analysis": f.get("semantic_analysis")} for f in git.get("changed_files", [])],
        },
        "attribute_impacts": [],
        "boundaries": ["Indexed requirement mentions are not verified JIRA-to-commit links", "Same-class callers are source evidence; HTTP endpoints require controller route proof", "No test execution or baseline evidence is asserted"],
    }
    for item in attribute_changes:
        attribute = item["attribute"]
        source_file = next((f for f in git.get("changed_files", []) if f.get("file_path") == item["changed_file"]), {})
        methods = item["functional_methods"]
        callers = [c for m in methods for c in _callers(git, source_file, m)]
        # Constant changes can affect a method even if the method's own tokens are unchanged.
        if not methods:
            methods = ["UNRESOLVED_CONSTANT_CONSUMER"]
        jira = lookup_attribute_jiras(db, attribute, project_path=str(git.get("git_root") or git.get("project_path") or ""))
        try:
            mapping = mapping_service.lineage(db, attribute)
        except Exception as exc:
            mapping = {"status": "MAPPING_LOOKUP_ERROR", "detail": type(exc).__name__}
        discrepancies = []
        if re.search(r"\{2\}", item["after"]) and re.search(r"\{2,3\}", item["before"]):
            for row in (mapping.get("mappings") or []):
                rule = " ".join(str(row.get(k) or "") for k in ("mapping_rule", "validation_rule", "comments"))
                if re.search(r"\b2\s*(?:-|–|to)\s*3\b", rule, re.I):
                    discrepancies.append({"classification": "MAPPING_RULE_DISCREPANCY", "attribute": attribute,
                                          "mapping_row": row, "source_rule": "exactly 2 letters", "mapping_rule": rule.strip(),
                                          "evidence": "Changed Java constant compared to stored mapping validation"})
        result["attribute_impacts"].append({
            **item,
            "behaviorally_affected_methods": methods,
            "same_class_callers": callers,
            "related_jira_requirements": jira.get("matches", []),
            "jira_lookup_status": jira.get("status"),
            "jira_lookup_scope": jira.get("project_scope"),
            "business_operations": [op for c in callers for op in _controller_operations(git, c["method"])],
            "jira_relation": "REQUIREMENT_LEVEL_ONLY",
            "mapping_lineage": mapping,
            "mapping_discrepancies": discrepancies,
            "mapping_validation_note": "Compare mapping rule with changed constant; discrepancy requires authoritative mapping row verification",
        })
    return result
