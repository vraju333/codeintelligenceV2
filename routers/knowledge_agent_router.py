from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.agent.knowledge_agent_service import KnowledgeAgentService
from collections import Counter, defaultdict
import re

router = APIRouter(prefix="/api/knowledge-agent", tags=["Knowledge Agent"])
service = KnowledgeAgentService()


class KnowledgeAgentRequest(BaseModel):
    query: str = Field(..., min_length=1)
    project_path: str | None = None


def format_git_evidence(evidence: dict) -> str:
    lines = [f"Commit: {evidence.get('commit', 'unknown')}",
             f"Message: {evidence.get('subject', 'unknown')}",
             f"Comparison: {evidence.get('comparison', 'unknown')} (first parent → HEAD)",
             "Explicit JIRA references: " + (", ".join(evidence.get('explicit_jira_references') or []) or "none")]
    files = evidence.get('changed_java_files') or []
    if not files:
        lines.append('No changed Java files in this commit.')
    for file in files:
        lines.append(f"\nFile: {file.get('path', '')} ({'test' if file.get('is_test') else 'production'})")
        for change in file.get('pattern_changes') or []:
            lines.append(f"  Regex changed: {change.get('before')} → {change.get('after')}")
        if file.get('test_methods'):
            lines.append('  JUnit test methods: ' + ', '.join(file['test_methods']))
        for change in (file.get('changed_lines') or [])[:35]:
            lines.append('  ' + change)
    lines.append('Affected HTTP endpoints: not verified from this commit diff alone; requires call-graph evidence.')
    lines.append('JUnit tests are candidates, not proof of endpoint coverage. Working-tree changes excluded.')
    return '\n'.join(lines)


def format_qa_evidence(records: dict, db: Session | None = None, project: str = '', jira: str = '') -> str:
    """Deduplicate by Jira/test ID, retain source provenance, reconcile recorded runs."""
    from services.agent.sdlc_report_service import unique_cases, scope
    from sdlc_models import SdlcTestExecution
    cases = unique_cases(records.get('test_cases') or [])
    latest = {}
    if db is not None and project:
        query = db.query(SdlcTestExecution)
        if jira:
            query = query.filter(SdlcTestExecution.jira_key == jira.upper())
        for execution in query.order_by(SdlcTestExecution.id.desc()).all():
            if scope(execution.project_path) == scope(project):
                latest.setdefault((execution.jira_key.upper(), execution.test_id.casefold()), execution)
    lines = [f"Imported QA records: {records.get('count', len(records.get('test_cases') or []))}",
             f"Unique QA cases: {len(cases)}", "Multiple sources for the same test ID are consolidated; imports do not prove execution."]
    for case in cases:
        key = (str(case.get('jira') or jira).upper(), case['test_id'].casefold())
        execution = latest.get(key)
        status = execution.status if execution else 'NOT_VERIFIED'
        lines.append(f"- {case['test_id']}: {case.get('scenario') or ''} | input={case.get('input') or '(blank)'} | expected={case.get('expected') or ''} | execution={status}")
        lines.append('  Sources: ' + '; '.join(f"{x.get('kind')}: {x.get('filename')} ({x.get('reference')})" for x in case['sources']))
        if execution:
            lines.append(f"  Recorded evidence: {execution.evidence_ref}; recorded by {execution.recorded_by}")
        if case['conflicting_source_content']:
            lines.append('  WARNING: conflicting test definitions across sources')
    return '\n'.join(lines)


def historical_jira_answer(db: Session, query: str, jira_keys: tuple[str, ...], include_qa: bool) -> dict:
    """Source-verified historical evidence, independent of current working tree."""
    from config import settings
    from services.agent.jira_commit_trace_service import JiraCommitTraceService, format_trace
    from services.agent.commit_call_path_service import analyse_commit_paths, format_commit_paths
    from services.knowledge.test_suite_service import TestSuiteService

    trace_service = JiraCommitTraceService()
    project = str(getattr(settings, 'JAVA_PROJECT_PATH', '') or '')
    sections, traces, paths, qa_results = [], {}, {}, {}
    for jira in jira_keys:
        trace = trace_service.analyse(jira)
        path_data = analyse_commit_paths(trace_service, trace)
        traces[jira] = trace
        paths[jira] = path_data
        sections.append(format_trace(trace) + '\n\n' + format_commit_paths(path_data))
        if include_qa and project:
            records = TestSuiteService().search(db, project_path=project, jira=jira, limit=500)
            qa_results[jira] = records
            sections.append('IMPORTED QA EVIDENCE FOR ' + jira + '\n' +
                            (format_qa_evidence(records, db=db, project=project, jira=jira) if records.get('count') else
                             'No imported QA records found for this Jira and project.'))
    return {
        'status': 'FOUND' if any(t.get('commits') for t in traces.values()) else 'NOT_VERIFIED',
        'query': query,
        'source': 'HISTORICAL_JIRA_COMMIT_EVIDENCE',
        'answer': '\n\n'.join(sections),
        'git_commit_evidence': traces,
        'commit_call_paths': paths,
        'test_suites': qa_results,
        'execution_verified': False,
        'note': 'Git commit links and call paths are source-scoped; imported QA plans do not prove execution.',
    }


def combined_attribute_impact(db: Session, query: str, attribute: str, jira: str = "") -> dict:
    """Aggregate independent evidence for multi-domain ASK; never manufacture links."""
    from config import settings
    from services.agent.ask_mapping_documents_service import mapping_documents_answer
    from services.knowledge.test_suite_service import TestSuiteService
    from services.scenario.scenario_baseline_service import ScenarioBaselineService
    from services.agent.ask_evidence_service import attribute_change_history

    result = {"query": query, "attribute": attribute, "source": "COMBINED_ATTRIBUTE_EVIDENCE",
              "status": "PARTIAL_MATCH", "evidence_warnings": []}
    parts = []
    def collect(label, fn):
        try:
            return fn()
        except Exception as exc:
            result["evidence_warnings"].append(f"{label} unavailable: {type(exc).__name__}")
            return None

    mapping = collect("Mapping", lambda: mapping_documents_answer(db, f"mapping documents for {attribute}"))
    if isinstance(mapping, dict):
        result["mapping_rows"] = mapping.get("mapping_rows") or []
        result["mapping_documents"] = mapping.get("mapping_documents") or []
        parts.append(str(mapping.get("answer") or ""))

    code = collect("Engineering", lambda: attribute_change_history(db, query, attribute, include_qa=False))
    if isinstance(code, dict):
        result["engineering_evidence"] = code
        result["verified_change_jiras"] = code.get("verified_change_jiras") or []
        result["candidate_jiras"] = code.get("candidate_jiras") or []
        result["attribute_verified_paths"] = [p for e in code.get("evidence") or [] for p in e.get("attribute_verified_paths") or []]
        result["unverified_attribute_paths"] = [p for e in code.get("evidence") or [] for p in e.get("unverified_attribute_paths") or []]
        for key in ("git_evidence", "git_commit_evidence", "commit_call_paths", "jira_issues", "historical_jiras", "affected_methods", "affected_apis"):
            if key in code:
                result[key] = code[key]
        parts.append(str(code.get("answer") or ""))

    # Predictive questions need current-source candidates even without historical Git evidence.
    # Keep them separate from attribute_verified_paths and execution evidence.
    if re.search(r"\b(?:if i change|if we change|what if|could be affected|might be affected|potential(?:ly)? affected)\b", query, re.I):
        from services.agent.project_attribute_impact_service import project_attribute_impact
        predictive = collect("Predictive Java impact", lambda: project_attribute_impact(db, query, attribute))
        if isinstance(predictive, dict):
            result["predictive_impact"] = predictive
            result["predictive_api_candidates"] = [p for p in predictive.get("reverse_call_paths", []) if p.get("endpoint")]
            result["predictive_scenario_candidates"] = predictive.get("scenario_candidates") or []
            result["predictive_evidence_status"] = "LEXICAL_CANDIDATES_NOT_VERIFIED"
            endpoints = sorted({str(p.get("http_method") or "") + " " + str(p.get("endpoint") or "") for p in result["predictive_api_candidates"]})
            scenarios = sorted({str(c.get("scenario_code") or "") for c in result["predictive_scenario_candidates"]})
            parts.append("Predictive API candidates (NOT VERIFIED): " + (", ".join(endpoints) or "none found in available source"))
            parts.append("Predictive scenario candidates (NOT EXECUTED): " + (", ".join(scenarios) or "none linked by project and endpoint"))
            parts.append("Predictive results are current-source lexical candidates, not actual Git changes, proven attribute dataflow or test executions.")

    project = str(getattr(settings, "JAVA_PROJECT_PATH", "") or getattr(settings, "PYTHON_PROJECT_PATH", "") or "")
    result["qa_project_scope"] = project
    result["evidence_contract_version"] = "2.0"
    result["confidence_policy"] = "SOURCE_VERIFIED_OR_EXPLICITLY_UNVERIFIED"
    if project:
        qa = collect("Imported QA", lambda: TestSuiteService().search(
            db, project_path=project, jira=jira, attribute=attribute, limit=500))
        if isinstance(qa, dict):
            result["test_suites"] = qa
            result["qa_evidence_status"] = "IMPORTED_PLANS_NOT_EXECUTION"
            parts.append(format_qa_evidence(qa, db=db, project=project, jira=jira) if qa.get("count") else
                         "No matching imported QA documents in the configured project.")
    else:
        result["evidence_warnings"].append("QA search requires a configured project path")

    baseline_service = ScenarioBaselineService()
    overview = collect("Scenario baselines", lambda: baseline_service.get_overview(db))
    if isinstance(overview, list):
        # The overview is already restricted to the active project by the existing service.
        # These are project baselines, NOT verified attribute-linked baselines.
        result["project_release_baselines"] = overview
        result["project_release_baseline_scope"] = "ACTIVE_PROJECT_NOT_ATTRIBUTE_VERIFIED"
        parts.append(f"Active-project scenario baselines: {len(overview)} (not verified as related to {attribute}).")
    if jira:
        coverage = collect("JIRA test baseline coverage", lambda: baseline_service.get_jira_coverage(db, jira))
        if isinstance(coverage, dict):
            result["jira_test_baselines"] = coverage.get("items") or []
            parts.append(f"JIRA-linked testing baselines for {jira}: {coverage.get('covered_count', 0)}.")
    result["execution_verified"] = False
    result["answer"] = "\n\n".join(p for p in parts if p)
    result["status"] = "FOUND" if any((result.get("mapping_rows"), result.get("test_suites", {}).get("count") if isinstance(result.get("test_suites"), dict) else 0, result.get("jira_test_baselines"), result.get("engineering_evidence", {}).get("status") == "FOUND" if isinstance(result.get("engineering_evidence"), dict) else False)) else "NOT_FOUND"
    return result


def _canonical_jira(query: str) -> str:
    """Accept CAS-2, CAS2 and CAS 2 without changing unrelated words."""
    import re
    match = re.search(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_]{1,9})[\s-]?(\d{1,7})(?![A-Za-z0-9])", query)
    return f"{match.group(1).upper()}-{match.group(2)}" if match else ""


def _requested_evidence(query: str) -> set[str]:
    import re
    terms = {
        "jira": r"\b(?:jira|issues?|tickets?|requirements?)\b",
        "git": r"\b(?:git|commits?|changes?|code|methods?|classes?|apis?|endpoints?)\b",
        "mapping": r"\b(?:mappings?|mapped|documents?|workbooks?|excel|xml)\b",
        "test": r"\b(?:tests?|testing|qa|coverage|regression|test\s+cases?)\b",
        "release": r"\b(?:releases?|versions?|baselines?|history|historical)\b",
    }
    return {key for key, pattern in terms.items() if re.search(pattern, query, re.I)}


def _jira_combined_evidence(db: Session, query: str, jira: str, requested: set[str]) -> dict:
    """Deterministic JIRA-first retrieval; graph absence does not erase SQL/Git evidence."""
    from config import settings
    from services.agent.ask_evidence_service import jira_impact, detect_attribute
    from services.knowledge.test_suite_service import TestSuiteService
    from services.scenario.scenario_baseline_service import ScenarioBaselineService
    from services.agent.jira_commit_trace_service import JiraCommitTraceService, format_trace
    from services.agent.commit_call_path_service import analyse_commit_paths, format_commit_paths

    output = {"status": "PARTIAL_MATCH", "query": query, "jira": jira,
              "source": "JIRA_COMBINED_EVIDENCE", "evidence_warnings": [], "execution_verified": False}
    answers = []
    def fetch(label, fn):
        try:
            return fn()
        except Exception as exc:
            output["evidence_warnings"].append(f"{label}: {type(exc).__name__}")
            return None

    engineering = fetch("Git and Java", lambda: jira_impact(db, (jira,), include_qa=False))
    if isinstance(engineering, dict):
        output["engineering_evidence"] = engineering
        answers.append(str(engineering.get("answer") or ""))
        for entry in engineering.get("evidence") or []:
            output.setdefault("affected_methods", []).extend(
                sorted({x.get("method") for x in entry.get("semantic_changes") or [] if x.get("method")}))
            output.setdefault("affected_apis", []).extend(entry.get("source_verified_endpoints") or [])
            output.setdefault("git_commits", []).extend(entry.get("git_commits") or [])
    trace_service = JiraCommitTraceService()
    trace = fetch("Commit trace", lambda: trace_service.analyse(jira))
    if isinstance(trace, dict):
        output["git_commit_evidence"] = {jira: trace}
        if trace.get("commits"):
            answers.append(format_trace(trace))
            paths = fetch("Commit call paths", lambda: analyse_commit_paths(trace_service, trace))
            if isinstance(paths, dict):
                output["commit_call_paths"] = {jira: paths}
                answers.append(format_commit_paths(paths))

    attribute = detect_attribute(query)
    project = str(getattr(settings, "JAVA_PROJECT_PATH", "") or getattr(settings, "PYTHON_PROJECT_PATH", "") or "")
    if "mapping" in requested and attribute:
        from services.agent.ask_mapping_documents_service import mapping_documents_answer
        mapping = fetch("Mapping", lambda: mapping_documents_answer(db, f"mapping documents for {attribute}"))
        if isinstance(mapping, dict):
            output["mapping_rows"] = mapping.get("mapping_rows") or []
            output["mapping_documents"] = mapping.get("mapping_documents") or []
    if project and ("test" in requested or "release" in requested):
        qa = fetch("Imported QA", lambda: TestSuiteService().search(db, project_path=project, jira=jira, limit=500))
        if isinstance(qa, dict):
            output["test_suites"] = qa
            answers.append(format_qa_evidence(qa, db=db, project=project, jira=jira))
    if "release" in requested or "test" in requested:
        baseline = ScenarioBaselineService()
        coverage = fetch("JIRA test baselines", lambda: baseline.get_jira_coverage(db, jira))
        if isinstance(coverage, dict):
            output["jira_test_baselines"] = coverage.get("items") or []
            output["jira_baseline_coverage"] = coverage
        if "release" in requested:
            overview = fetch("Project release baselines", lambda: baseline.get_overview(db))
            if isinstance(overview, list):
                output["project_release_baselines"] = overview
                output["project_release_baseline_scope"] = "ACTIVE_PROJECT_NOT_JIRA_VERIFIED"
    output["answer"] = "\n\n".join(x for x in answers if x)
    if output.get("git_commits") or (trace or {}).get("commits") or output.get("jira_test_baselines"):
        output["status"] = "FOUND"
    output["note"] = "Project-wide baselines are not verified JIRA coverage; QA imports are not test execution."
    return output


def _jira_requirement_impact(db: Session, query: str, jira: str, selected_project: str | None = None) -> dict:
    """Read-only, selected-project JIRA impact, including unimplemented requirements."""
    from config import settings
    from routers.requirement_impact_router import ImpactRequest, analyze

    project = str(selected_project or getattr(settings, "JAVA_PROJECT_PATH", "") or
                  getattr(settings, "PYTHON_PROJECT_PATH", "") or "")
    if not project:
        return {"status": "PROJECT_REQUIRED", "query": query, "jira": jira,
                "source": "JIRA_REQUIREMENT_IMPACT",
                "answer": "Select an indexed project before analyzing this JIRA."}
    result = analyze(ImpactRequest(mode="jira", value=jira, project=project), db)
    result["query"] = query
    result["source"] = "JIRA_REQUIREMENT_IMPACT"
    result["intent"] = "JIRA_IMPACT_ANALYSIS"
    result["impact_type"] = "PROPOSED_REQUIREMENT_AND_SOURCE_CANDIDATES"
    issue = result.get("jira") or {}
    if result.get("status") == "JIRA_NOT_INDEXED":
        result["answer"] = f"{jira} is not indexed for the selected project. Sync its JIRA project in Knowledge Hub."
        return result
    lines = [f"Requirement impact analysis for {jira}",
             f"Summary: {issue.get('summary') or '(not provided)'}",
             f"Requirement: {issue.get('requirement') or '(not provided)'}"]
    candidates = (result.get("code_impact") or {}).get("candidates") or []
    scenarios = result.get("scenarios") or []
    tests = result.get("test_cases") or []
    commits = result.get("git_commits") or []
    lines.append(f"Code impact candidates: {len(candidates)} (not automatically verified changes)")
    lines.append(f"Matching existing scenarios: {len(scenarios)}")
    lines.append(f"Imported test cases: {len(tests)} (not proof of execution)")
    lines.append(f"Explicit JIRA-linked Git commits: {len(commits)}")
    if not commits:
        lines.append("No matching implementation commit was verified; this can be a new, unimplemented requirement.")
    java = result.get("java_evidence") or {}
    lines.append("Existing Java source scan: " + str(java.get("status", "NOT_CHECKED")))
    methods = java.get("methods") or []
    lines.append(f"Source methods mentioning requirement attributes: {len(methods)} (potential impact, not verified JIRA dependency)")
    for item in methods[:25]:
        lines.append(f"  {item.get('file')}:{item.get('line')} — {item.get('method')}() | attributes: {', '.join(item.get('matched_attributes') or [])}")
    for item in (java.get("fields") or [])[:12]:
        lines.append(f"Source field: {item.get('file')}:{item.get('line')} — {item.get('text')}")
    for item in (java.get("database_annotations") or [])[:12]:
        lines.append(f"Persistence annotation: {item.get('file')}:{item.get('line')} — {item.get('text')} (schema unverified)")
    operations = result.get("affected_operations") or []
    ranked = result.get("operation_evidence") or {}
    likely = ranked.get("potentially_related") or []
    other = ranked.get("other_discovered") or []
    lines.append(f"Discovered HTTP entry points: {len(operations)}; source-backed call candidates: {len(likely)}; other endpoints: {len(other)}")
    for item in likely[:12]:
        lines.append(f"  POTENTIAL: {item.get('http_method')} {item.get('endpoint')} — {item.get('controller_method')} (receiver-aware source call candidate; not AST)")
        for path in item.get('candidate_call_paths', [])[:5]:
            lines.append("    candidate chain: " + " -> ".join(f"{n.get('class')}.{n.get('method')}() [{n.get('file')}:{n.get('line')}]" for n in path.get('path', [])))
        for target in item.get('candidate_method_links', [])[:5]:
            lines.append(f"    source path endpoint: {target.get('file')}:{target.get('line')}::{target.get('method')} — {', '.join(target.get('matched_attributes') or [])}")
    for item in other[:12]:
        lines.append(f"  DISCOVERED ONLY: {item.get('http_method')} {item.get('endpoint')} — no matched source call path")
    review = ranked.get('role_review') or {}
    lines.append('Requirement role review (inferred, not verified changes):')
    for item in review.get('priority_operations', []) + review.get('conditional_or_other_operations', []):
        lines.append(f"  {item.get('change_review_priority')} | {item.get('http_method')} {item.get('endpoint')} | roles: {', '.join(item.get('requirement_roles') or [])} | source path: {item.get('source_path_found')}")
        lines.append('    ' + str(item.get('role_reason')))
    for gap in ranked.get('mapping_source_gaps', []):
        lines.append(f"Mapping-to-source review gap: {gap.get('attribute')} — {gap.get('classification')} (bounded scan, not proof of absence)")
    for item in (result.get("mappings") or [])[:12]:
        lines.append(f"Imported mapping: {item.get('source_path')} → {item.get('target_class')}.{item.get('target_attribute')} (document evidence)")
    for item in candidates[:10]:
        if isinstance(item, dict):
            lines.append("Inferred code candidate: " + str(item.get("method") or item.get("file") or item.get("path") or item.get("title") or "unspecified"))
    for item in scenarios[:10]:
        lines.append("Scenario text match: " + str(item.get("scenario_code") or item.get("name") or ""))
    for item in (result.get("regression_recommendations") or [])[:10]:
        lines.append("Recommended, NOT EXECUTED: " + str(item.get("scenario")))
    lines.append("No JIRA-to-method call graph, database schema, or test execution is implied by source mentions.")
    result["answer"] = "\n".join(lines)
    return result


def _ask_knowledge_agent_impl(payload: KnowledgeAgentRequest, db: Session = Depends(get_db)):
    try:
        # Historical QA test questions return actual imported records, never generated evidence.
        import re
        from config import settings
        from services.knowledge.test_suite_service import TestSuiteService
        query = payload.query
        requested = _requested_evidence(query)
        jira_key = _canonical_jira(query)
        # Normalize informal JIRA keys before any DSL, graph or LLM route.
        # An explicit JIRA request must not fall through to graph-only search.
        # Negated historical terms must never select the historical Git route.
        # Source-analysis intent takes priority for proposed JIRA requirements.
        historical_exclusion = bool(re.search(
            r"\b(?:do\s+not|don't|without|exclude|ignore|no)\s+(?:use\s+|analy[sz]e\s+|include\s+)?(?:git\s+)?(?:history|commits?|git)\b",
            query, re.I))
        explicit_historical = bool(re.search(
            r"\b(?:show|find|list|analy[sz]e|trace|compare|retrieve)\b.{0,55}"
            r"\b(?:historical|history|commits?|git\s+history|release\s+history|baseline\s+history)\b",
            query, re.I))
        historical_only = explicit_historical and not historical_exclusion
        # A bare JIRA key or simple "Analyze CAS-6" request should use the same
        # comprehensive pre-development impact route as the detailed Swagger query.
        # Historical requests retain their dedicated evidence route.
        short_jira_impact = bool(jira_key and re.fullmatch(
            r"(?:analy[sz]e|check|review|impact(?: analysis)?(?: for| of)?|show impact(?: for| of)?)?\s*"
            + re.escape(jira_key) + r"(?:\s+(?:before development|for impact analysis))?\s*[.!]?",
            query.strip(), re.I))
        predevelopment = bool(short_jira_impact) or bool(re.search(
            r"\b(?:pre.development|before (?:development|implementation)|not (?:yet )?implemented|"
            r"existing java|java methods?|source references?|controllers?|rest apis?|"
            r"api operations?|database fields?|data mappings?|regression scenarios?|"
            r"potentially affected|could be affected|impact|impacted|trace|lineage|"
            r"(?:first|last|full)\s*name|firstName|lastName|fullName)\b", query, re.I))
        if jira_key and predevelopment and not historical_only:
            return _jira_requirement_impact(db, query, jira_key, payload.project_path)
        if jira_key and not re.search(r"\b(?:latest|last)\s+(?:commit|committed)\b", query, re.I):
            return _jira_combined_evidence(db, query, jira_key, requested)
        from services.agent.ask_evidence_service import detect_attribute
        impact_attribute = detect_attribute(query)
        # Complete attribute history is a multi-source evidence request even when
        # the question does not explicitly name mapping, QA, or release domains.
        # Keep operation/endpoint impact questions on the existing impact route.
        complete_history = bool(re.search(
            r"\b(?:complete|full|entire|all)\s+(?:change\s+)?history\b|"
            r"\bhistory\s+(?:of|for)\s+.*\b(?:everything|all evidence)\b",
            query, re.I))
        if impact_attribute and complete_history:
            result = combined_attribute_impact(db, query, impact_attribute)
            result["source"] = "COMPLETE_ATTRIBUTE_HISTORY"
            result["history_scope"] = "CURRENT_MAPPING_AND_QA_PLUS_AVAILABLE_HISTORICAL_EVIDENCE"
            result["note"] = ("Current mapping and imported QA evidence are not proof of "
                              "historical mapping revisions or test executions. "
                              "Only source-verified history is presented as historical changes.")
            return result
        if impact_attribute and ("release" in requested or "test" in requested) and len(requested) >= 2:
            return combined_attribute_impact(db, query, impact_attribute)
        # Explicit commit-to-Jira correlation request; do not intercept ordinary Jira ASK.
        correlation_query = bool(re.search(r"\b(?:possible|potential|suggest|likely|related|associate|correlat)\w*\b", query, re.I)) and bool(re.search(r"\b(?:jira|issue|ticket)\b", query, re.I)) and bool(re.search(r"\b(?:commit|sha|git)\b", query, re.I))
        if correlation_query:
            sha_match = re.search(r"\b[0-9a-fA-F]{7,40}\b", query)
            if sha_match:
                from services.agent.jira_git_suggestion_service import correlate_commit
                result = correlate_commit(db, sha_match.group(0))
                links = result['explicit_links']
                suggestions = result['possible_related_jiras']
                lines = ["JIRA / Git correlation for " + result['commit'][:10]]
                lines += ["Explicit reference: " + item['jira'] + " (" + ', '.join(e['source'] for e in item['evidence']) + ")" for item in links]
                lines += ["Possible related JIRA: " + item['jira'] + " (" + str(item['days_apart']) + " days; shared terms: " + ', '.join(item['matching_terms']) + ")" for item in suggestions]
                if not links and not suggestions:
                    lines.append("No supported JIRA association found in available evidence.")
                lines.append("PR lookup: " + result['github_pr_status'] + "; Jira dates: " + result['jira_date_lookup_status'])
                result['answer'] = '\n'.join(lines)
                return result
        # Project-scoped deterministic route for Java attribute impact. Prevent
        # global RAG/LLM results from mixing Employee/Student workspaces.
        from services.agent.ask_mapping_documents_service import extract_attribute as _extract_mapping_attribute, mapping_change_impact_intent as _mapping_change_intent
        _impact_attr = _extract_mapping_attribute(query)
        _java_impact = bool(re.search(r"\b(?:java methods?|impacted scenarios?|affected scenarios?|method impact)\b", query, re.I))
        if _impact_attr and (_java_impact or _mapping_change_intent(query)):
            from services.agent.project_attribute_impact_service import project_attribute_impact
            return project_attribute_impact(db, query, _impact_attr, payload.project_path)
        # Broad impact questions must not be hijacked by the mapping-only or QA-only fast paths.
        from services.agent.ask_evidence_service import detect_attribute
        impact_attribute = detect_attribute(query)
        requested_domains = sum(bool(re.search(pattern, query, re.I)) for pattern in (
            r"\b(?:mapping|mappings|mapped|workbook|excel)\b",
            r"\b(?:jira|requirements?|issues?)\b",
            r"\b(?:git|commits?|methods?|apis?|code)\b",
            r"\b(?:tests?|qa|baselines?|releases?|regression)\b",
        ))
        if impact_attribute and requested_domains >= 3 and re.search(r"\b(?:impact|complete|full|including|all|traceability)\b", query, re.I):
            jira_key_match = re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", query, re.I)
            return combined_attribute_impact(db, query, impact_attribute,
                                             jira=jira_key_match.group(0).upper() if jira_key_match else "")
        # Mapping change impact is cross-source, not a mapping-only lookup.
        from services.agent.ask_mapping_documents_service import extract_attribute, mapping_change_impact_intent
        if mapping_change_impact_intent(query):
            mapping_attribute = extract_attribute(query)
            if mapping_attribute:
                return combined_attribute_impact(db, query, mapping_attribute)
        # Mapping-document intent takes precedence over attribute historical-change routing.
        # Never substitute a Git commit when the user requests mapping workbooks.
        from services.agent.ask_mapping_documents_service import mapping_document_intent, mapping_documents_answer
        if mapping_document_intent(query):
            return mapping_documents_answer(db, query)
        # Recency of Jira requirements must be decided by Jira creation timestamps,
        # not by whichever Jira has a verified Git commit.
        from services.agent.latest_attribute_jira_service import is_latest_jira_question, latest_attribute_jira
        if is_latest_jira_question(query):
            return latest_attribute_jira(db, query)
        from services.agent.ask_query_dsl import parse_query, optional_llm_parser
        dsl = parse_query(query, model=optional_llm_parser())
        if dsl.intent == 'JIRA_HISTORICAL_IMPACT':
            from services.agent.ask_evidence_service import jira_impact
            result = jira_impact(db, dsl.jira_keys, include_qa=dsl.include_qa)
            result['query_dsl'] = dsl.to_dict()
            return result
        # Attribute modification questions require a matching historical semantic diff,
        # not merely a requirement mentioning the attribute.
        from services.agent.ask_evidence_service import detect_attribute, is_change_question, attribute_change_history
        attribute = detect_attribute(query)
        if attribute and not re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", query, re.I) and is_change_question(query):
            return attribute_change_history(db, query, attribute, include_qa=bool(re.search(r'\b(?:test|qa|coverage|regression)\b', query, re.I)))
        # Cross-domain requests must reach LangGraph; QA is supplementary evidence,
        # not a terminal routing decision. Keep the standalone QA fast path below.
        jira_match = re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", query, re.I)
        composite_request = bool(jira_match) and (
            bool(re.search(r"\bend[ -]to[ -]end\b|\bfull (?:impact|traceability)\b", query, re.I))
            or (
                bool(re.search(r"\b(?:git|commit|changed java|changed method|call.graph)\b", query, re.I))
                and bool(re.search(r"\b(?:test case|test suite|qa|coverage)\b", query, re.I))
            )
        )
        if composite_request:
            # Run the engineering agent FIRST so QA keywords cannot suppress
            # Git/JIRA/static/graph analysis. Never synthesize missing evidence.
            from services.agent.jira_commit_trace_service import JiraCommitTraceService, format_trace
            git_service = JiraCommitTraceService()
            git_trace = git_service.analyse(jira_match.group(0))
            from services.agent.commit_call_path_service import analyse_commit_paths, format_commit_paths
            commit_paths = analyse_commit_paths(git_service, git_trace)
            analysis = service.run(db, query)
            if not isinstance(analysis, dict):
                analysis = {'status': 'FOUND', 'answer': str(analysis)}
            project = str(getattr(settings, 'JAVA_PROJECT_PATH', None) or getattr(settings, 'PYTHON_PROJECT_PATH', None) or '')
            if project:
                records = TestSuiteService().search(
                    db, project_path=project, jira=jira_match.group(0), limit=500
                )
                qa_answer = format_qa_evidence(records, db=db, project=project, jira=jira) if records.get('count') else (
                    'No matching imported QA test cases found for this project and JIRA.'
                )
                engineering_answer = str(analysis.get('answer') or '').strip()
                analysis['answer'] = (
                    format_trace(git_trace) + '\n\n' + format_commit_paths(commit_paths) + '\n\nENGINEERING IMPACT ANALYSIS (candidate context; commit-scoped evidence above takes precedence)\n' +
                    (engineering_answer or 'Engineering analysis returned no narrative; inspect tool_results.') +
                    '\n\nIMPORTED QA EVIDENCE (independent of code impact)\n' + qa_answer
                )
                analysis['git_commit_evidence'] = git_trace
                analysis['commit_call_paths'] = commit_paths
                analysis['test_suites'] = records
                analysis['execution_verified'] = False
                analysis['source'] = 'ENGINEERING_AND_IMPORTED_QA'
            return analysis
        # A committed change must be compared against its parent, not the working tree.
        if re.search(r"\b(latest|last|most recent)\s+(?:committed\s+)?(?:commit|committed changes)\b|\blatest committed changes\b", query, re.I):
            from services.agent.latest_commit_evidence_service import LatestCommitEvidenceService
            evidence = LatestCommitEvidenceService().analyse()
            return {'status': 'FOUND', 'query': query, 'source': 'LOCAL_GIT_LATEST_COMMIT',
                    'answer': format_git_evidence(evidence),
                    'git_evidence': evidence}
        if re.search(r"\b(test\s*(?:suite|case|data|scenario)s?|qa\s+coverage)\b", query, re.I):
            jira_match = re.search(r"\b[A-Z][A-Z0-9_]+-\d+\b", query, re.I)
            attr_match = re.search(r"\b(countryCode|accountType|primaryEmail|gpa)\b", query, re.I)
            canonical_attribute = detect_attribute(query) or (attr_match.group(0) if attr_match else '')
            project = str(getattr(settings, 'JAVA_PROJECT_PATH', None) or getattr(settings, 'PYTHON_PROJECT_PATH', None) or '')
            if project:
                records = TestSuiteService().search(db, project_path=project,
                    jira=jira_match.group(0) if jira_match else '',
                    attribute=canonical_attribute, limit=500)
                if records['count']:
                    return {'status': 'FOUND', 'query': query, 'source': 'IMPORTED_QA_TEST_SUITES',
                        'answer': format_qa_evidence(records, db=db, project=project, jira=jira_match.group(0) if jira_match else ''),
                        'test_suites': records, 'execution_verified': False}
                return {'status': 'NOT_FOUND', 'query': query, 'source': 'IMPORTED_QA_TEST_SUITES',
                        'answer': 'No matching imported QA test cases found for the configured project. Verify project scope, parsed case count, and source workbook headers. Mapping documents are not QA test cases.',
                        'test_suites': records, 'execution_verified': False}
        return service.run(db, payload.query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ask")
def ask_knowledge_agent(payload: KnowledgeAgentRequest, db: Session = Depends(get_db)):
    """Keep established evidence retrieval, add a validated presentation contract."""
    from services.agent.ask_presentation_planner import plan_presentation
    result = _ask_knowledge_agent_impl(payload, db)
    if not isinstance(result, dict):
        return result
    # Presentation only: never change the underlying evidence or imply verified links.
    result["presentation_plan"] = plan_presentation(payload.query, result)
    return result
