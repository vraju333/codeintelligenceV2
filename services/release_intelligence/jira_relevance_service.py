"""Conservative, project-scoped JIRA candidates for changed Java attributes.

Candidate relevance is advisory and NEVER creates scenario/JIRA relationships.
"""
from __future__ import annotations

import re
from pathlib import Path

from config import settings
from db_models import JiraKnowledge


class JiraRelevanceService:
    @staticmethod
    def _normalize_path(value):
        return str(value or '').replace('\\', '/').rstrip('/').casefold()

    @staticmethod
    def _tokens(value):
        # Preserve Java camelCase identifiers as well as their individual words.
        raw = re.findall(r'[A-Za-z][A-Za-z0-9_]*', str(value or ''))
        result = set()
        for token in raw:
            result.add(token.casefold())
            parts = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', token).replace('_', ' ').split()
            result.update(p.casefold() for p in parts if len(p) >= 3)
        return result

    def find(self, db, changed_attributes, changed_classes, explicit_ids=()):
        project = self._normalize_path(Path(settings.JAVA_PROJECT_PATH).resolve())
        attrs = {str(a).casefold() for a in changed_attributes if str(a).strip()}
        if not attrs:
            return []
        explicit = {str(x).upper() for x in explicit_ids}
        results = []
        for row in db.query(JiraKnowledge).all():
            if self._normalize_path(row.project_path) != project:
                continue
            jira_id = str(row.jira_id or '').strip().upper()
            if not jira_id or jira_id in explicit:
                continue
            # Jira metadata may be appended after the human requirement. Never
            # treat labels, issue status, etc. as business/code evidence.
            requirement = re.split(r'\n\s*Jira metadata\s*:', str(row.requirement or ''), maxsplit=1, flags=re.I)[0]
            text = f'{row.title or ""}\n{requirement}'
            tokens = self._tokens(text)
            matches = sorted(a for a in attrs if a in tokens or self._tokens(a).issubset(tokens))
            if not matches:
                continue
            results.append({
                'jira_id': jira_id,
                'title': row.title,
                'matched_attributes': matches,
                'evidence_type': 'REQUIREMENT_ATTRIBUTE_TEXT_MATCH',
                'relationship': 'INFERRED_CANDIDATE_NOT_LINKED',
                'reason': 'Stored requirement mentions a changed attribute; implementation and scenario linkage have not been verified.',
                'confidence': 'LOW',
            })
        return sorted(results, key=lambda item: (-len(item['matched_attributes']), item['jira_id']))
