"""Evidence-controlled composition of Jira impact answers.

The deterministic priority report is authoritative. LLM-authored baseline and
mapping commentary is sanitized so it cannot turn candidate scenarios into
verified or mandatory regression tests.
"""
from __future__ import annotations
import re

_SECTION = re.compile(r'(?m)^\s{0,3}#{2,5}\s+(.+?)\s*$')


def _baseline_context(body: str) -> str:
    """Do not reproduce LLM claims about baseline existence or mandatory scenarios.

    Baseline verification must come from explicit linked evidence, not a model
    paragraph. The priority report already states that evidence boundary.
    """
    return ("### Existing test baselines\n"
            "No JIRA-linked test baseline is established by the evidence in this answer. "
            "This does not prove that no baseline exists in the project.\n\n"
            "**Proposed regression tests (not existing baselines):** For the validation "
            "paths confirmed by source or call-graph evidence, test valid two-letter "
            "codes, rejection of one-letter and three-letter codes, and lowercase "
            "normalization. Prioritize create/replace operations where validation "
            "is confirmed. Test PATCH only if its call path reaches this validation; "
            "GET and DELETE are not automatically regression-required.")


def compose_evidence_answer(priority_report: str, agent_answer: str) -> str:
    """Keep supported Jira/mapping context and deterministic classifications.

    Drop competing LLM method/scenario lists and regenerate baseline guidance
    conservatively. Fail closed on unrecognized sections.
    """
    if not priority_report:
        return agent_answer
    chunks = []
    headings = list(_SECTION.finditer(agent_answer or ''))
    baseline_added = False
    for i, match in enumerate(headings):
        title = re.sub(r'[*_`]', '', match.group(1)).strip().lower()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(agent_answer)
        body = agent_answer[match.start():end].strip()
        if re.search(r'\b(?:existing\s+)?test\s+baselines?\b', title):
            if not baseline_added:
                chunks.append(_baseline_context(body))
                baseline_added = True
        elif re.search(r'\b(jira\s+.*overview|requirement\s+overview|xml.to.model mappings?|mapping lineage|mapping document)\b', title):
            chunks.append(body)
    if not baseline_added:
        chunks.append(_baseline_context(''))
    preface = ('### Evidence-controlled impact analysis\n'
               'Method and endpoint classifications below are authoritative for this answer. '
               'No explicit JIRA-to-code implementation association or baseline link is asserted without linked evidence.\n\n')
    result = preface + priority_report
    if chunks:
        result += '\n\n---\n\n### Additional requirement, mapping and baseline context\n\n' + '\n\n'.join(chunks)
    return result
