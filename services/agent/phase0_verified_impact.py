"""Read-only, working-tree grounded Java attribute evidence for Ask answers.

No database writes, index changes, or graph mutations. This is deliberately
conservative: call sites are not promoted to verified graph edges.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

JAVA_METHOD = re.compile(
    r"(?m)^\s*(?:@[\w.]+(?:\([^\n]*\))?\s*)*"
    r"(?:(?:public|private|protected|static|final|synchronized|abstract|native)\s+)*"
    r"[\w<>\[\], ?]+\s+([a-zA-Z_$][\w$]*)\s*\([^;{}]*\)"
    r"\s*(?:throws\s+[\w., ]+)?\s*\{"
)


def requested_attribute(query: str) -> str | None:
    # The query may mention more than one attribute; avoid arbitrary guesses.
    patterns = [
        r"\b(?:attribute|field|property)\s+[`'\"]?([a-z][A-Za-z0-9_]*)",
        r"\b(?:reference|references|transform|transforms|validate|validates|impact|impacts)\s+[`'\"]?([a-z][A-Za-z0-9_]*)",
    ]
    stop = {"the", "which", "methods", "java", "directly", "in", "of", "and", "for", "code", "a", "or", "reference", "references", "transform", "validate", "impact"}
    for pattern in patterns:
        for match in re.finditer(pattern, query, re.I):
            if match.group(1).lower() not in stop:
                return match.group(1)
    return None


def _method_spans(source: str):
    for match in JAVA_METHOD.finditer(source):
        opening = source.find("{", match.start(), match.end())
        depth = 0
        closing = None
        for i in range(opening, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    closing = i + 1
                    break
        if closing is not None:
            yield match.group(1), opening + 1, closing


def inspect_java_attribute(project_root: str | None, attribute: str, max_files: int = 2000) -> dict[str, Any]:
    """Verify method statements from live files, not model-generated excerpts.

    Does not attempt to prove a call graph; a syntactic call site is distinct
    from a resolved AST edge. Does not follow symlinks outside the project.
    """
    result: dict[str, Any] = {"attribute": attribute, "status": "UNAVAILABLE", "direct": [], "call_sites": []}
    if not project_root or not re.fullmatch(r"[A-Za-z_$][\w$]*", attribute):
        return result
    root = Path(project_root).expanduser()
    if not root.is_dir():
        return result
    files = sorted(root.rglob("*.java"))
    if len(files) > max_files:
        result["status"] = "TOO_MANY_FILES"
        return result
    for path in files:
        if not path.is_file() or path.is_symlink() or any(part in {"target", "build", ".gradle", ".git"} for part in path.parts):
            continue
        try:
            data = path.read_bytes()
            if len(data) > 1_000_000:
                continue
            source = data.decode("utf-8", errors="replace")
        except OSError:
            continue
        if attribute.lower() not in source.lower():
            continue
        lines = source.splitlines()
        for method, start, end in _method_spans(source):
            body = source[start:end]
            if not re.search(r"(?:\b" + re.escape(attribute) + r"\b|\b(?:get|set)" + re.escape(attribute[0].upper() + attribute[1:]) + r"\s*\()", body, re.I):
                continue
            for hit in re.finditer(r"(?:\b" + re.escape(attribute) + r"\b|\b(?:get|set)" + re.escape(attribute[0].upper() + attribute[1:]) + r"\s*\()", body, re.I):
                line_no = source.count("\n", 0, start + hit.start()) + 1
                line = lines[line_no - 1].strip()
                if line.startswith(("//", "*", "/*")) or not line:
                    continue
                kind = "DIRECT_SOURCE"
                if re.search(r"\b(?:set|get)" + re.escape(attribute[0].upper() + attribute[1:]) + r"\s*\(", line):
                    kind = "DIRECT_SOURCE"
                item = {"method": path.stem + "." + method, "file": str(path.relative_to(root)),
                        "line": line_no, "statement": line, "classification": kind,
                        "source_kind": "LIVE_WORKING_TREE", "sha256": hashlib.sha256(data).hexdigest()}
                if item not in result["direct"]:
                    result["direct"].append(item)
    result["status"] = "VERIFIED_LIVE_SOURCE" if result["direct"] else "NO_DIRECT_MATCHES"
    return result


def render_direct_impact(evidence: dict[str, Any]) -> str:
    rows = evidence.get("direct") or []
    if not rows:
        return "No direct method references verified from live working-tree files. Indexed or inferred candidates require separate validation."
    out = ["### Direct Java source evidence (deterministically verified)",
           "These are direct source references, **not inferred candidates**. File/line numbers refer to the live working tree."]
    seen = set()
    for item in rows:
        key = (item["method"], item["line"], item["file"])
        if key in seen:
            continue
        seen.add(key)
        out.append(f"- **{item['method']}** — `DIRECT_SOURCE` — `{item['file']}:{item['line']}` — `{item['statement']}`")
    out += ["", "### Call graph and downstream impact",
            "No `VERIFIED_CALL_GRAPH` edges are asserted by this live-source scan. "
            "A syntactic call site alone does not prove a resolved graph edge; use AST/call-graph evidence for those edges. "
            "Other downstream relationships remain `INFERRED_CANDIDATE` until independently verified."]
    return "\n".join(out)
