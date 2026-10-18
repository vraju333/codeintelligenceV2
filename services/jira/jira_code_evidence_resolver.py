"""Read-only, project-scoped JIRA -> Java candidate resolver.

No commit required. Scores are deterministic relevance scores, NOT calibrated probabilities.
Only structured, project-scoped explicit associations can verify implementation ownership.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from db_models import JiraKnowledge

_STOP = {"the", "and", "for", "with", "from", "into", "must", "should", "have", "that", "this", "only", "update", "change", "customer", "service", "java", "code", "field", "value", "values", "attribute", "validation", "validate", "allow", "accept", "new", "use", "using", "data"}
_JIRA = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_METHOD = re.compile(r"\b(?:public|protected|private)\s+(?:(?:static|final|synchronized|abstract)\s+)*(?:[\w<>\[\], ?]+)\s+(\w+)\s*\(")


def _words(value: str) -> set[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value or "")
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]*", value) if len(w) > 1}


def _terms(value: str) -> set[str]:
    return _words(value) - _STOP


def _safe_project(project_path: str) -> Path:
    root = Path(project_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("project_path must be an existing directory")
    return root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _changed_lines(root: Path, base_ref: str, target_ref: str) -> list[dict[str, Any]]:
    for ref in (base_ref, target_ref):
        if not re.fullmatch(r"[A-Za-z0-9_./^-]{1,120}", ref) or ref.startswith("-"):
            raise ValueError("Invalid Git reference")
        _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    patch = _git(root, "diff", "--no-ext-diff", "--unified=0", base_ref, target_ref, "--", "*.java")
    changes = []; filename = None; line_no = 0
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            filename = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,\d+)?", line)
            if m: line_no = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++") and filename:
            changes.append({"file": filename, "line": line_no, "statement": line[1:]})
            line_no += 1
        elif line.startswith(" ") and filename:
            line_no += 1
    return changes


def _method_for_line(lines: list[str], line_number: int) -> str | None:
    for i in range(min(line_number - 1, len(lines) - 1), -1, -1):
        m = _METHOD.search(lines[i])
        if m: return m.group(1)
    return None


def _source_lines(root: Path, max_files: int = 1500, max_bytes: int = 1_000_000) -> list[dict[str, Any]]:
    """Read current Java files; skip build outputs and avoid symlink escape."""
    results = []
    excluded = {".git", ".idea", "target", "build", "out", ".gradle", "node_modules", ".venv"}
    count = 0
    for source in sorted(root.rglob("*.java")):
        if any(part in excluded for part in source.relative_to(root).parts): continue
        if not source.is_file() or not source.resolve().is_relative_to(root): continue
        count += 1
        if count > max_files: raise ValueError("Java file scan limit exceeded; narrow the project path")
        if source.stat().st_size > max_bytes: continue
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        lines = content.decode("utf-8", errors="replace").splitlines()
        method = None
        for number, statement in enumerate(lines, 1):
            m = _METHOD.search(statement)
            if m: method = m.group(1)
            if not method or not statement.strip() or statement.lstrip().startswith(("//", "*", "import ", "package ")): continue
            results.append({"file": str(source.relative_to(root)), "line": number, "statement": statement.strip(), "method_candidate": method, "sha256": digest, "evidence_type": "LIVE_JAVA_SOURCE", "method_provenance": "SOURCE_HEURISTIC_NOT_AST"})
    return results


def _row_text(row: Any) -> str:
    return " ".join(str(getattr(row, key, "") or "") for key in ("title", "requirement", "description", "summary"))


def _explicit_links(row: Any, root: Path, links: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Only caller-supplied structured trusted links; never promote raw source mentions."""
    output = []
    for link in links or []:
        if not isinstance(link, dict): continue
        if str(link.get("jira_id", "")).upper() != str(row.jira_id).upper(): continue
        if not link.get("verified") or not link.get("evidence_id"): continue
        if link.get("source_type") not in ("REVIEWED_ASSOCIATION", "TRACKED_CHANGE_RECORD", "APPROVED_MAPPING"): continue
        if not link.get("method") and not link.get("file"): continue
        if not link.get("project_path"): continue
        try:
            if Path(str(link["project_path"])).expanduser().resolve() != root: continue
        except (ValueError, OSError): continue
        output.append({"jira_id": row.jira_id, "file": link.get("file"), "method": link.get("method"), "classification": "VERIFIED", "relationship": "EXPLICIT_IMPLEMENTATION_ASSOCIATION", "evidence_id": link["evidence_id"], "source_type": link["source_type"]})
    return output


def resolve_jira_code_candidates(db, project_path: str, base_ref: str | None = None, target_ref: str = "HEAD", jira_ids: list[str] | None = None, explicit_links: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    root = _safe_project(project_path)
    requested = {v.upper() for v in jira_ids or []}
    if any(not _JIRA.fullmatch(v) for v in requested): raise ValueError("Invalid JIRA ID")
    scoped = []
    for row in db.query(JiraKnowledge).all():
        if requested and str(row.jira_id).upper() not in requested: continue
        if not getattr(row, "project_path", None): continue
        if Path(row.project_path).expanduser().resolve() != root: continue
        scoped.append(row)
    mode = "GIT_DIFF" if base_ref else "LIVE_SOURCE"
    if base_ref:
        if not (root / ".git").exists(): raise ValueError("Git diff mode requires a Git repository")
        evidence = _changed_lines(root, base_ref, target_ref)
        for item in evidence:
            source = (root / item["file"]).resolve()
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines() if source.is_file() and source.is_relative_to(root) else []
            item["method_candidate"] = _method_for_line(lines, item["line"]) if target_ref == "HEAD" else None
            item["method_provenance"] = "WORKTREE_HEURISTIC" if target_ref == "HEAD" else "NOT_RESOLVED"
            item["evidence_type"] = "GIT_DIFF_ADDED_LINE"
    else:
        evidence = _source_lines(root)
    candidates = []; verified = []
    for row in scoped:
        verified.extend(_explicit_links(row, root, explicit_links))
        requirement = _terms(_row_text(row))
        matched = []
        for item in evidence:
            # Require at least one multi-token identifier (countryCode) matching requirement,
            # or an exact non-generic field token. Prevent generic 'update' matches.
            identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9]*", item["statement"])
            matches = []
            for ident in identifiers:
                tokens = _terms(ident.replace("_", " "))
                if len(tokens) >= 2 and tokens.issubset(requirement): matches.append(ident)
                elif len(tokens) == 1 and tokens.issubset(requirement) and len(next(iter(tokens))) >= 6:
                    matches.append(ident)
            if not matches: continue
            # Deterministic relevance, not statistical probability. Strong identifier match
            # + validation/mapping context; intentionally conservative.
            score = 0.65
            context = f'{item["file"]} {item.get("method_candidate") or ""} {item["statement"]}'.lower()
            if any(w in context for w in ("validate", "matcher", "pattern", "set", "mapper", "require")): score += 0.15
            if mode == "GIT_DIFF": score += 0.10
            matched.append({**item, "attribute_identifiers": sorted(set(matches)), "match_score": round(min(score, 0.90), 2)})
        matched.sort(key=lambda x: (-x["match_score"], x["file"], x["line"]))
        if matched:
            candidates.append({"jira_id": row.jira_id, "title": getattr(row, "title", None), "classification": "INFERRED", "relationship": "POSSIBLE_IMPLEMENTATION", "explicit_jira_to_code_link": False, "match_score": matched[0]["match_score"], "score_kind": "HEURISTIC_RELEVANCE_NOT_PROBABILITY", "verified_facts": ["JIRA_REQUIREMENT_RECORD", "GIT_CHANGED_LINES" if base_ref else "LIVE_JAVA_SOURCE"], "confirmation_required": True, "source_evidence": matched[:40], "source_evidence_total": len(matched)})
    return {"project_path": str(root), "mode": mode, "base_ref": base_ref, "target_ref": target_ref if base_ref else None, "candidate_count": len(candidates), "candidates": candidates, "verified_associations": verified, "unmatched_jiras": [r.jira_id for r in scoped if r.jira_id not in {c["jira_id"] for c in candidates} and r.jira_id not in {v["jira_id"] for v in verified}], "policy": "Explicit trusted structured associations are VERIFIED; source/attribute overlaps are INFERRED. Scores are relevance heuristics, not calibrated probabilities.", "limitations": ["Live source scanning uses method-declaration heuristics, not a resolved Java AST or call graph.", "Only exact attribute-token matches are supported; mapping workbook synonyms are not yet joined.", "No database or knowledge graph writes occur; explicit links must come from a trusted validated system, not arbitrary user claims."]}
