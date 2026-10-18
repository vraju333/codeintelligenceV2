"""Small, validated ASK intent DSL. Never treat model output as evidence.

High-confidence Jira queries are resolved without an LLM. Optional model parsing
handles ambiguous phrasing; its output is validated and may only select an intent.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Callable

JIRA = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*-\d+)(?![A-Za-z0-9_])')
CURRENT = re.compile(r'\b(uncommitted|unstaged|staged|working[ -]tree|local changes|not committed|pending commit|since head)\b', re.I)
CHANGE = re.compile(r'\b(chang(?:e|es|ed|ing)|diff|commit|implementation|implement(?:ed|ation)?|code|method|class|impact(?:ed)?|affect(?:ed)?|regression|apis?|endpoints?|trace|historical|history|git)\b', re.I)
QA = re.compile(r'\b(qa|test(?:s|ing| cases?| suites?)?|coverage|regression)\b', re.I)
INDEX = re.compile(r'\b(indexed|indexing|index status|stored requirement)\b', re.I)

@dataclass(frozen=True)
class AskQuery:
    intent: str = 'GENERAL'
    jira_keys: tuple[str, ...] = ()
    include_qa: bool = False
    origin: str = 'RULES'

    def to_dict(self):
        return asdict(self)


def parse_query(query: str, model: Callable[[str], str] | None = None) -> AskQuery:
    query = (query or '').strip()
    jiras = tuple(dict.fromkeys(m.group(1).upper() for m in JIRA.finditer(query)))
    if CURRENT.search(query):
        return AskQuery('CURRENT_CHANGES', jiras, bool(QA.search(query)))
    if jiras and INDEX.search(query) and not CHANGE.search(query):
        return AskQuery('JIRA_INDEX_STATUS', jiras)
    if jiras and CHANGE.search(query):
        return AskQuery('JIRA_HISTORICAL_IMPACT', jiras, bool(QA.search(query)))
    if not model:
        return AskQuery('GENERAL', jiras)
    # Only allow the model to classify; never let it invent Jira identifiers.
    prompt = ('Classify this CodeIntelligence query. Return ONLY JSON with intent one of '
              'GENERAL, JIRA_HISTORICAL_IMPACT, CURRENT_CHANGES and include_qa boolean. '
              'A Jira question about code changes refers to historical committed changes unless '
              'the user explicitly says working tree/uncommitted. Do not invent identifiers.\nQuery: '
              + json.dumps(query))
    try:
        raw = model(prompt)
        data = json.loads(re.search(r'\{[\s\S]*\}', str(raw)).group())
        intent = data.get('intent')
        if intent not in ('GENERAL', 'JIRA_HISTORICAL_IMPACT', 'CURRENT_CHANGES'):
            raise ValueError('invalid intent')
        if intent == 'JIRA_HISTORICAL_IMPACT' and not jiras:
            return AskQuery('GENERAL', jiras)
        return AskQuery(intent, jiras, bool(data.get('include_qa')), 'LLM')
    except (ValueError, TypeError, AttributeError):
        return AskQuery('GENERAL', jiras)


def optional_llm_parser() -> Callable[[str], str] | None:
    """Opt-in classification only; no code or evidence leaves the local backend."""
    if os.getenv('ASK_DSL_LLM_ENABLED', 'false').lower() != 'true':
        return None
    def classify(prompt: str) -> str:
        from services.agent.knowledge_agent_service import KnowledgeAgentService
        response = KnowledgeAgentService._build_model().invoke(prompt)
        return str(getattr(response, 'content', response))
    return classify
