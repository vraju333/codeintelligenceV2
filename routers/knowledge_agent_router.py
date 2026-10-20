from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from services.agent.knowledge_agent_service import KnowledgeAgentService
from collections import Counter, defaultdict

router = APIRouter(prefix="/api/knowledge-agent", tags=["Knowledge Agent"])
service = KnowledgeAgentService()


class KnowledgeAgentRequest(BaseModel):
    query: str = Field(..., min_length=1)


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
                            (format_qa_evidence(records, db=db, project=project, jira=jira_match.group(0)) if records.get('count') else
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


@router.post("/ask")
def ask_knowledge_agent(payload: KnowledgeAgentRequest, db: Session = Depends(get_db)):
    try:
        # Historical QA test questions return actual imported records, never generated evidence.
        import re
        from config import settings
        from services.knowledge.test_suite_service import TestSuiteService
        query = payload.query
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
                qa_answer = format_qa_evidence(records, db=db, project=project, jira=jira_match.group(0)) if records.get('count') else (
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
