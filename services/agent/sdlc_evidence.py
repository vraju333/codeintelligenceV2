"""Deterministic source-coverage reporting for historical SDLC questions.

Never equate current Git NO CHANGES with historical absence, or a failed/not-run
lookup with a negative historical fact. This layer does not create evidence.
"""
from __future__ import annotations

import json
import re
from typing import Any

_HISTORY = ("baseline", "histor", "release", "version", "audit", "scenario history", "sdlc", "traceability", "change records")


def is_project_version_request(query: str) -> bool:
    q = query.lower()
    return bool(re.search(r"\b(?:compare|comparison|difference|diff)\b", q)
                and re.search(r"\b(?:captured|project|release|baseline)\b", q)
                and re.search(r"\bversions?\b", q)
                and not re.search(r"\b(?:mapping family|mapping versions?|workbook versions?)\b", q))


def is_sdlc_history_request(query: str) -> bool:
    q = query.lower()
    return is_project_version_request(query) or any(x in q for x in _HISTORY) and (
        "jira" in q or bool(re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", query))
        or "captured" in q or "test baseline" in q or "release history" in q
    )


def sdlc_attribute(query: str) -> str | None:
    # Explicit code-style attributes only; do not mistake CAS-2 for an attribute.
    names = re.findall(r"\b[a-z][a-z0-9_]*[A-Z][A-Za-z0-9_]*\b", query)
    names += re.findall(r"`([a-z][A-Za-z0-9_]*)`", query)
    return next((x for x in names if x.lower() not in {"customerid", "jiraid"}), None)


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _has_error(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("error") or value.get("exception") or value.get("is_error")
                    or value.get("status") in {"ERROR", "FAILED"})
    if isinstance(value, str):
        return bool(re.search(r"(?i)^(?:error|exception|tool execution error|invalid input):", value.strip()))
    return False


def _count(payload: Any, name: str) -> tuple[int | None, str]:
    if name == "project_version_comparison" and isinstance(payload, dict):
        comparisons = payload.get("comparisons")
        if isinstance(comparisons, list):
            return len(comparisons), "operation comparisons (inspect status for snapshot availability)"
    if name == "sdlc_audit_history" and isinstance(payload, dict):
        rows = payload.get("captured_test_baselines")
        if isinstance(rows, list):
            return len(rows), "captured JIRA-linked test baselines (release archives separately reported)"
    if name == "baseline_history" and isinstance(payload, dict):
        history = payload.get("historical_traceability")
        linked = payload.get("jira_linked_baselines")
        if isinstance(history, list) and isinstance(linked, list):
            return len(history) + len(linked), "attribute traceability and explicitly JIRA-linked baselines"
        if isinstance(history, list):
            return len(history), "historical_traceability"
        return None, "no structured history collection"
    if name == "git_history" and isinstance(payload, dict):
        rows = payload.get("matches")
        if isinstance(rows, list):
            return len(rows), f"commit-message matches (scanned {payload.get('scanned_commits', '?')} recent commits)"
    if name == "scenario_impact" and isinstance(payload, dict):
        rows = payload.get("affected_scenarios")
        if isinstance(rows, list):
            return len(rows), "affected_scenarios (candidates only)"
    if name == "mapping_lineage" and isinstance(payload, dict):
        for key in ("matches", "rows", "mappings", "lineage"):
            if isinstance(payload.get(key), list):
                return len(payload[key]), key
    # Graph/unified payloads may be nested and have different schemas. Never
    # claim an exhaustive negative based on an unknown response structure.
    return None, "response retrieved; match count not independently validated"


def compose_sdlc_report(query: str, tool_results: list[dict[str, Any]], priority_report: str = "") -> str:
    expected = ["live_jira_issue", "baseline_history", "scenario_impact", "graph_search", "unified_knowledge_search"]
    if "mapping" in query.lower() or "end-to-end" in query.lower():
        expected.append("mapping_lineage")
    if is_project_version_request(query):
        expected.append("project_version_comparison")
    if any(k in query.lower() for k in ("baseline", "release", "audit", "history", "end-to-end")):
        expected.append("sdlc_audit_history")
    if any(k in query.lower() for k in ("git", "commit", "change records", "audit", "release history")):
        expected.append("git_history")
    grouped: dict[str, list[Any]] = {}
    for row in tool_results:
        name = str(row.get("name") or "")
        grouped.setdefault(name, []).append(_decode(row.get("content")))
    lines = ["### SDLC evidence and source coverage", "", "| Source | Status | Evidence |", "|---|---|---|"]
    for name in dict.fromkeys(expected):
        payloads = grouped.get(name, [])
        if not payloads:
            lines.append(f"| `{name}` | NOT_QUERIED | No successful tool result recorded for this request |")
            continue
        successes = [p for p in payloads if not _has_error(p)]
        if not successes:
            lines.append(f"| `{name}` | ERROR | Tool returned an error; absence cannot be concluded |")
            continue
        counts = [_count(p, name) for p in successes]
        known = [n for n, _ in counts if n is not None]
        if known:
            n = sum(known)
            status = "FOUND" if n else "NO_MATCH"
            detail = f"{n} record(s) in {counts[0][1]}"
        else:
            status, detail = "QUERIED_UNVERIFIED", "Returned data; relationships/count not independently verified"
        lines.append(f"| `{name}` | {status} | {detail} |")
        if name == "project_version_comparison":
            for payload in successes:
                if isinstance(payload, dict):
                    lines.append(f"| comparison scope | {payload.get('status', 'UNKNOWN')} | {payload.get('scope', 'Operation captures only')}; project `{payload.get('project_path', 'unknown')}`; diagnostic `{payload.get('diagnostic', 'not supplied')}`; captures {payload.get('captured_baseline_count', 'unknown')} |")
                    if not payload.get('comparisons') and not payload.get('insufficient'):
                        lines.append("| capture diagnostic | NO_COMPARABLE_OPERATIONS | No operation baseline groups were returned for the active project; verify project path and captured operations |")
                if isinstance(payload, dict):
                    for item in payload.get("comparisons", []):
                        lines.append(f"| operation `{item.get('operation')}` | {item.get('status')} | Captured versions {item.get('versions')}; snapshot keys {item.get('changed_snapshot_keys', 'unavailable')}; source changes {item.get('source_changes', 'unavailable')} |")
                    for item in payload.get("insufficient", []):
                        lines.append(f"| operation `{item.get('operation')}` | INSUFFICIENT_VERSIONS | Captured: {item.get('captured_versions')} |")
        if name == "sdlc_audit_history":
            for payload in successes:
                if isinstance(payload, dict):
                    lines.append(f"| release archives | {'FOUND' if payload.get('release_archives') else 'NO_MATCH'} | {len(payload.get('release_archives', []))} scoped archive(s); not necessarily linked to JIRA |")
    jira = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", query.upper())
    if jira:
        lines.extend(["", f"**JIRA:** `{jira.group(0)}`. A retrieved JIRA requirement is not proof of implementation, testing, or release linkage."])
    if priority_report:
        lines.extend(["", priority_report])
    lines.extend(["", "**Historical interpretation:** `NO_MATCH` applies only to the queried source and scope. "
                  "`NOT_QUERIED`, `ERROR`, and `QUERIED_UNVERIFIED` do not establish absence. "
                  "A clean current Git working tree does not prove that no historical commits or releases exist. "
                  "Existing code and proposed regression scenarios are not completed implementation or captured test baselines."])
    if is_project_version_request(query):
        lines.extend(["", "**Project version comparison:** Mapping-document versions are separate from "
                      "captured project/test-baseline versions. No project-version diff is asserted "
                      "without two identified captured versions and their stored snapshots."])
    return "\n".join(lines)
