"""Conservative, deterministic guardrails for agent-facing evidence claims.

This module does not create or modify evidence. Unknown relationships remain unknown.
"""
from __future__ import annotations

import json
import re
from typing import Any

from services.agent.phase0_verified_impact import inspect_java_attribute, requested_attribute, render_direct_impact

JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


def _payloads(tool_results: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    parsed = []
    for row in tool_results:
        value = row.get("content")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        parsed.append((str(row.get("name") or ""), value))
    return parsed


def _explicit_jira_coverage(payload: Any, jira: str) -> bool:
    """Only accept explicit Jira IDs on a captured baseline or test link.

    Generic scenario lists, endpoint discovery, text mentions and LLM prose
    are not evidence of confirmed coverage.
    """
    if isinstance(payload, list):
        return any(_explicit_jira_coverage(v, jira) for v in payload)
    if not isinstance(payload, dict):
        return False
    ids = payload.get("jira_ids") or payload.get("linked_jira_ids") or []
    if isinstance(ids, str):
        ids = re.findall(r"[A-Z][A-Z0-9]+-\d+", ids)
    if jira in ids and any(payload.get(key) for key in (
        "test_baseline_id", "scenario_test_baseline_id", "test_report_id",
        "explicit_coverage_link_id", "assertion_evidence_id",
    )):
        return True
    for key, value in payload.items():
        if key in {"content", "description", "text", "summary", "answer", "source"}:
            continue
        if isinstance(value, (list, dict)) and _explicit_jira_coverage(value, jira):
            return True
    return False


def assess(query: str, tool_results: list[dict[str, Any]], project_root: str | None = None) -> dict[str, Any]:
    jira_ids = sorted(set(JIRA_KEY.findall(query or "")))
    payloads = _payloads(tool_results)
    confirmed = {jira: any(_explicit_jira_coverage(p, jira) for _, p in payloads) for jira in jira_ids}
    live_sources = []
    for name, payload in payloads:
        if name != "java_source_search" or not isinstance(payload, dict):
            continue
        for match in payload.get("exact_matches") or []:
            if match.get("evidence") == "CURRENT_JAVA_SOURCE" and match.get("file_path") and match.get("sha256"):
                live_sources.append({"path": match["file_path"], "sha256": match["sha256"], "source_kind": "LIVE_WORKING_TREE"})
    # The unified traceability tool already verifies the Git snapshot, changed
    # constant, source caller sites, controller annotations and mapping provenance.
    # Do not run a second, attribute-guessing scanner against a possibly different
    # configured project root and then claim that evidence is unavailable.
    unified = next((payload for name, payload in reversed(payloads)
                    if name == "current_change_traceability" and isinstance(payload, dict)), None)
    verified_unified = bool(
        unified and unified.get("status") == "FOUND"
        and any(item.get("evidence_classification") == "SOURCE_CONSTANT_AND_GETTER_USAGE"
                for item in (unified.get("attribute_impacts") or []))
        and (unified.get("git_evidence") or {}).get("git_root")
    )
    impact = None
    if not verified_unified and re.search(r"(?i)\b(?:direct|reference|transform|call.graph|downstream)\b", query):
        attribute = requested_attribute(query)
        if attribute:
            impact = inspect_java_attribute(project_root, attribute)
    historical_tools = sorted({name for name, _ in payloads if name in {
        "baseline_history", "git_change_analysis", "git_sdlc_traceability",
        "mapping_history", "mapping_source_lineage", "mapping_graph_lineage",
    }})
    return {
        "verified_java_impact": impact,
        "verified_current_change_traceability": unified if verified_unified else None,
        "historical_tools_executed": historical_tools,
        "jira_coverage": {jira: ("CONFIRMED_EXPLICIT_LINK" if found else "NOT_CONFIRMED_FROM_RETURNED_EVIDENCE") for jira, found in confirmed.items()},
        "live_source_files": live_sources,
        "rules": {
            "scenario": "Scenario existence or shared call flow is not confirmed Jira test coverage.",
            "source": "Only an exact file read with fingerprint proves a live working-tree source snapshot.",
            "confidence": "Do not invent numerical confidence percentages.",
        },
    }


def guard_answer(query: str, answer: str, evidence: dict[str, Any]) -> str:
    """Remove unsupported confidence and coverage claims; show deterministic verdict."""
    answer = str(answer or "")
    # Unsupported numerical confidence is never authoritative.
    answer = re.sub(r"(?im)^\s*[-*]?\s*\*{0,2}(?:overall\s+)?confidence(?:\s+level)?\*{0,2}\s*:\s*(?:high\s*)?\(?\d+(?:\.\d+)?\s*%\)?[^\n]*$",
                    "Confidence: Not quantitatively established by retrieved evidence.", answer)
    if any(status != "CONFIRMED_EXPLICIT_LINK" for status in evidence["jira_coverage"].values()):
        # Do not leave an unverified affirmative sentence in the answer. This is
        # intentionally conservative; explicit test coverage can still be shown
        # after a baseline/test link is returned by a tool.
        answer = re.sub(r"(?im)^([^\n]*(?:scenarios?|tests?|coverage)[^\n]*\b(?:confirm(?:s|ed)?|verified|proven)\b[^\n]*)$",
                        "", answer)
        answer = re.sub(r"(?im)^([^\n]*\b(?:confirmed|verified)\s+(?:test\s+)?(?:scenarios?|coverage)[^\n]*)$",
                        "", answer)
    # Attribute/source overlap is not a trusted Jira-to-implementation link.
    # A model sometimes labels all matched methods as "Verified JIRA-to-Code Links".
    # Rewrite that unsupported heading and its explanatory sentence conservatively.
    if evidence.get("jira_coverage"):
        answer = re.sub(
            r"(?im)^(\s*[-*]?\s*\*{0,2})Verified JIRA-to-Code Links(\*{0,2}\s*:)",
            r"\1Source/attribute matches (JIRA implementation link unverified)\2",
            answer,
        )
        answer = re.sub(
            r"(?im)^([ \t]*[-*]?[ \t]*)The methods and mappings listed above are directly related to the `?countryCode`? attribute as per the JIRA requirement\.?",
            r"\1Methods and mappings reference countryCode; this does not verify a CAS-2-to-code implementation link.",
            answer,
        )
    # History absence must not be asserted without actually querying Git/baselines.
    historical = set(evidence.get("historical_tools_executed") or [])
    if re.search(r"(?i)\b(?:history|historical|previous|baseline|git)\b", query):
        if "baseline_history" not in historical:
            answer = re.sub(r"(?im)^([^\n]*(?:no|none|not found|no matches)[^\n]*(?:historical versions?|baselines?)[^\n]*)$", "", answer)
        if not any(name.startswith("git_") for name in historical):
            answer = re.sub(r"(?im)^([^\n]*(?:no|none|not found|no matches)[^\n]*(?:Git history|Git versions?)[^\n]*)$", "", answer)
    # Remove lines that became empty after suppressing unsupported claims.
    answer = re.sub(r"\n{3,}", "\n\n", answer).strip()
    notes = []
    unified = evidence.get("verified_current_change_traceability")
    impact = evidence.get("verified_java_impact")
    if impact and impact.get("status") == "VERIFIED_LIVE_SOURCE":
        # Never keep the LLM's contradictory inferred-method lists alongside
        # verified source statements. Use only the deterministic result.
        answer = render_direct_impact(impact)
    elif impact:
        notes.append("Direct-impact verification: live working-tree evidence was unavailable or had no matches; model-generated classifications are not verified.")
    if re.search(r"(?i)\b(?:history|historical|previous|baseline|git)\b", query):
        if "baseline_history" not in historical:
            notes.append("Captured baseline history: not verified by a baseline_history tool result in this request.")
        if not unified and not any(name.startswith("git_") for name in historical):
            notes.append("Git history: not verified by a Git tool result in this request.")
    for jira, status in evidence["jira_coverage"].items():
        if status != "CONFIRMED_EXPLICIT_LINK":
            notes.append(f"{jira}: No explicit Jira-to-test-baseline or test-evidence link verified in returned tool results. Discovered scenarios are candidates only.")
    if not unified and re.search(r"\b(current|latest|working.tree)\b", query, re.I) and not evidence["live_source_files"]:
        answer = re.sub(r"(?im)^(#{1,6}\s*)Current (?:Implementation|Source|Code)",
                        r"\1Unverified Current Implementation", answer)
        notes.append("Current-source provenance: not verified by an exact live-file read with fingerprint; retrieved code may be an indexed snapshot.")
    if not unified and not (impact and impact.get("status") == "VERIFIED_LIVE_SOURCE") and re.search(r"(?i)\b(direct|transform|downstream|call.graph|impact)\b", query):
        # This is a classification warning, not a rewrite of unsupported tool data.
        # Source statements must be verified independently of inferred call edges.
        notes.append("Impact classification: methods with exact source statements using the attribute (including mapper transformations and validation) are DIRECT_SOURCE; VERIFIED_CALL_GRAPH caller edges require call-graph evidence; remaining downstream paths are INFERRED_CANDIDATES.")
    if evidence["live_source_files"] and re.search(r"\b(current|latest|working.tree)\b", query, re.I):
        notes.append("Live-source fingerprint(s): " + "; ".join(f"{item['path']} (SHA-256 {item['sha256']})" for item in evidence["live_source_files"][:3]))
    if unified:
        # Distinguish what the deterministic joined tool proves from what it
        # explicitly does not prove. This is not a blanket assertion that all
        # downstream call-graph paths or JIRA links are verified.
        changes = unified.get("attribute_impacts") or []
        attributes = sorted({str(item.get("attribute")) for item in changes if item.get("attribute")})
        operations = [op for item in changes for op in (item.get("business_operations") or [])
                      if op.get("evidence") == "CONTROLLER_ANNOTATION_AND_SERVICE_METHOD_CALL"]
        notes.append("Unified current-change evidence: Git constant changes and Java getter usage are source-backed"
                     + (" for " + ", ".join(attributes) if attributes else "") + ".")
        if operations:
            notes.append("HTTP route evidence: controller annotations and service call sites were found for "
                         + ", ".join(sorted({str(op.get("http_method")) + " " + str(op.get("endpoint")) for op in operations}))
                         + "; these are source-level call sites, not independently resolved whole-program call-graph edges.")
        notes.append("Related indexed JIRAs are requirement-level matches only; no Jira-to-commit or test-baseline link is implied.")
    if notes:
        answer = "**Deterministic evidence checks**\n" + "\n".join("- " + note for note in notes) + "\n\n" + answer
    return answer
