"""Project-scoped, read-only ASK evidence assembly. No LLM can promote candidates to facts."""
from __future__ import annotations
import re
from config import settings
from services.agent.sdlc_report_service import build_report
from services.jira.attribute_jira_lookup_service import lookup
from services.agent.git_evidence_enrichment import enrich_commits, format_commit_history

ATTRIBUTES = {'countrycode': 'countryCode', 'country code': 'countryCode',
              'customeremail': 'customerEmail', 'customer email': 'customerEmail',
              'email validation': 'customerEmail', 'email': 'customerEmail',
              'accounttype': 'accountType', 'account type': 'accountType'}
CONSTANTS = {'countryCode': 'COUNTRY', 'customerEmail': 'EMAIL', 'accountType': 'ACCOUNT_TYPES'}


def detect_attribute(question: str) -> str | None:
    lowered = re.sub(r'[_-]', ' ', question).casefold()
    for spelling, canonical in sorted(ATTRIBUTES.items(), key=lambda p: -len(p[0])):
        if re.search(r'(?<!\w)' + re.escape(spelling) + r'(?!\w)', lowered):
            return canonical
    return None


def is_change_question(query: str) -> bool:
    return bool(re.search(r'\b(chang(?:e|es|ed)|modif(?:y|ied|ication)|implement(?:ed|ation)?|impact(?:ed)?|affect(?:ed)?|validation|code|git|commit|diff|history|historical)\b', query, re.I)) and not bool(re.search(r'\b(?:jiras?|requirements?)\s+(?:please|related|mentioning|for)?\s*\??$', query, re.I))


def _matching_change(change: dict, attribute: str) -> bool:
    constant = CONSTANTS.get(attribute, '').casefold()
    return bool(constant and str(change.get('constant') or '').casefold() == constant and change.get('kind') in ('PATTERN_CHANGED', 'DEPENDS_ON_CHANGED_PATTERN'))


def _summary(report: dict, *, attribute: str | None = None, include_qa: bool = False) -> dict:
    semantic = report['semantic_changes']
    matching = [s for s in semantic if _matching_change(s, attribute)] if attribute else semantic
    changed = [s for s in matching if s.get('kind') == 'PATTERN_CHANGED']
    methods = sorted({s['method'] for s in matching if s.get('method')})
    commits = report['git']['commits']
    # Attribute-to-code verification is semantic; Git/JIRA history is a separate,
    # explicitly linked evidence category. Do not discard non-pattern commits
    # (e.g. null-check changes) merely because they lack PATTERN_CHANGED.
    semantic_shas = {s['commit'] for s in matching} if attribute else {c['sha'] for c in commits}
    paths = sorted({p['endpoint'] for p in report['source_verified_paths'] if p['commit'] in semantic_shas})
    tests = report['qa']['test_cases'] if include_qa else []
    relevant_commits = commits  # Exact JIRA key in commit message, not inferred attribute change
    enriched = enrich_commits(report.get('project_path') or '', relevant_commits)
    lines = [f"JIRA: {report['jira']}", *format_commit_history(enriched)]
    if attribute:
        lines.append('Commit association: exact JIRA-key reference; only separately listed semantic changes prove an attribute-level modification.')
    for c in changed:
        lines.append(f"{c['constant']} validation: {c.get('before')} → {c.get('after')}")
    if attribute and not changed:
        lines.append(f'No commit-scoped {CONSTANTS.get(attribute, attribute)} validation modification verified for this JIRA.')
    lines.append('Affected Java methods: ' + (', '.join(methods) or 'not independently resolved'))
    lines.append('Source-verified APIs: ' + (', '.join(paths) or 'none verified'))
    if include_qa:
        lines.append(f"QA cases: {len(tests)} unique, {report['qa']['tests_with_recorded_execution']} with separately recorded execution evidence")
        for case in tests:
            lines.append(f"- {case['test_id']}: {case.get('scenario') or ''} | input={case.get('input') or '(blank)'} | expected={case.get('expected') or ''} | execution={case['execution_status']} | sources={len(case['sources'])}")
    lines.append('Evidence boundary: Git/Java paths are source-verified; QA imports are plans, not execution or approval. Recorded executions are user-supplied evidence.')
    return {'answer': '\n'.join(lines), 'semantic_changes': matching, 'source_verified_endpoints': paths,
            'qa': report['qa'] if include_qa else None, 'git_commits': enriched,
            'jira': report['jira'], 'matched_change': bool(changed) if attribute else bool(commits),
            'explicit_jira_commit_count': len(enriched)}


def jira_impact(db, jira_keys: tuple[str, ...], *, attribute: str | None = None, include_qa: bool = False) -> dict:
    project = str(getattr(settings, 'JAVA_PROJECT_PATH', '') or '')
    reports = [build_report(db, jira=jira, project=project, include_jira=False) for jira in jira_keys]
    entries = [_summary(report, attribute=attribute, include_qa=include_qa) for report in reports]
    return {'status': 'FOUND' if any(e['matched_change'] for e in entries) else 'NOT_VERIFIED',
            'source': 'SDLC_HISTORICAL_VERIFIED_EVIDENCE',
            'answer': '\n\n'.join(e['answer'] for e in entries),
            'jira_keys': list(jira_keys), 'evidence': entries,
            'note': 'Historical commit and Java source evidence, not current working-tree changes.'}


def attribute_change_history(db, query: str, attribute: str, *, include_qa: bool = False) -> dict:
    project = str(getattr(settings, 'JAVA_PROJECT_PATH', '') or '')
    candidates = lookup(db, attribute, project_path=project)
    verified = []
    examined = []
    for item in candidates.get('matches', []):
        key = item['jira_id']
        report = build_report(db, jira=key, project=project, include_jira=False)
        entry = _summary(report, attribute=attribute, include_qa=include_qa)
        examined.append(key)
        if entry['matched_change']:
            verified.append((key, entry))
    if not verified:
        answer = f'No historical {attribute} validation modification verified among {len(examined)} indexed Jira candidates. This is not proof that none exists outside indexed scope.'
    else:
        answer = '\n\n'.join(entry['answer'] for _, entry in verified)
    all_commits = []
    seen_shas = set()
    for _, entry in verified:
        for commit in entry['git_commits']:
            if commit['sha'] not in seen_shas:
                seen_shas.add(commit['sha'])
                all_commits.append(commit)
    return {'status': 'FOUND' if verified else 'NOT_VERIFIED', 'query': query,
            'source': 'ATTRIBUTE_TO_JIRA_TO_COMMIT_SEMANTIC', 'attribute': attribute,
            'candidate_jiras': examined, 'verified_change_jiras': [key for key, _ in verified],
            'answer': answer, 'evidence': [entry for _, entry in verified],
            'git_commits': all_commits,
            'git_commit_evidence': {key: {'commits': entry['git_commits'], 'association': 'EXPLICIT_JIRA_COMMIT_MESSAGE', 'attribute_semantic_change_verified': entry['matched_change']} for key, entry in verified},
            'coverage': candidates.get('coverage'),
            'note': 'Only a matching commit-scoped semantic modification qualifies; requirement mentions alone do not.'}
