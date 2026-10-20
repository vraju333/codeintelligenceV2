"""Read-only, evidence-labelled commit/Jira correlation.

No match inferred from timing alone is ever presented as a verified link.
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from config import settings
from db_models import JiraKnowledge

KEY = re.compile(r'(?<![A-Z0-9_])([A-Z][A-Z0-9_]*-\d+)(?![A-Z0-9_\-])', re.I)
STOP = {'the','and','for','with','from','this','that','change','changed','update','updated','code','jira','issue','fix','add','java','service','test'}


def _terms(value):
    value = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(value or ''))
    return {w for w in re.findall(r'[a-z][a-z0-9]{2,}', value.lower()) if w not in STOP}


def _git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=15)
    if p.returncode:
        raise ValueError('Git unavailable: ' + p.stderr.strip()[:200])
    return p.stdout.strip()


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _remote_repo(root):
    try:
        remote = _git(root, 'remote', 'get-url', 'origin')
    except ValueError:
        return None
    match = re.search(r'github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?$', remote)
    return (match.group(1), match.group(2)) if match else None


def _github_prs(root, sha):
    """Optional GitHub PR metadata; public repos or GITHUB_TOKEN for private repos."""
    repo = _remote_repo(root)
    if not repo:
        return [], 'NOT_CONFIGURED'
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    owner, name = repo
    url = f'https://api.github.com/repos/{owner}/{name}/commits/{sha}/pulls'
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'CodeIntelligence'}
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with urlopen(Request(url, headers=headers), timeout=8) as resp:
            data = json.load(resp)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return [], 'UNAVAILABLE'
    prs = []
    for pr in data[:30]:
        if not isinstance(pr, dict):
            continue
        prs.append({'number': pr.get('number'), 'title': pr.get('title') or '',
                    'body': pr.get('body') or '', 'url': pr.get('html_url'),
                    'branch': (pr.get('head') or {}).get('ref'),
                    'merge_commit_sha': pr.get('merge_commit_sha')})
    return prs, 'CHECKED'


def correlate_commit(db, sha: str, project_path: str | None = None, window_days: int = 7, github: bool = True):
    """Find explicit references, then plausible local JIRA candidates within ±window_days.

    JiraKnowledge.created_at is an ingestion timestamp, NOT Jira creation date.
    Therefore fallback uses live Jira's created_at when available and never uses
    the local record creation timestamp as a Jira creation timestamp.
    """
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', sha or ''):
        raise ValueError('A Git commit SHA (7-40 hex characters) is required')
    if not 0 <= window_days <= 30:
        raise ValueError('window_days must be between 0 and 30')
    root = Path(project_path or settings.JAVA_PROJECT_PATH or '').expanduser()
    if not root.is_dir():
        raise ValueError('Configured Java project directory is unavailable')
    full_sha = _git(root, 'rev-parse', '--verify', f'{sha}^{{commit}}')
    raw = _git(root, 'show', '-s', '--format=%s%n%b%n%aI', full_sha).splitlines()
    commit_date = _parse_date(raw[-1] if raw else None)
    message = '\n'.join(raw[:-1])
    files = _git(root, 'diff-tree', '--root', '--no-commit-id', '--name-only', '-r', full_sha).splitlines()
    # Branch refs are only evidence if they actually contain this commit.
    refs = _git(root, 'branch', '-a', '--contains', full_sha, '--format=%(refname:short)').splitlines()
    prs, pr_status = _github_prs(root, full_sha) if github else ([], 'NOT_REQUESTED')
    explicit = {}
    def add(text, source, ref=None):
        for key in KEY.findall(text or ''):
            key = key.upper()
            evidence = {'source': source, 'reference': ref or (text or '')[:200]}
            if evidence not in explicit.setdefault(key, []):
                explicit[key].append(evidence)
    add(message, 'COMMIT_MESSAGE', full_sha)
    for ref in refs:
        add(ref, 'BRANCH_NAME', ref)
    for pr in prs:
        add(pr['title'], 'PR_TITLE', pr['url'])
        add(pr['body'], 'PR_DESCRIPTION', pr['url'])
        add(pr['branch'], 'PR_BRANCH', pr['url'])
    # Only Jira creation timestamps from the live Jira API are eligible for window matching.
    candidates = []
    date_status = 'NOT_CHECKED'
    if commit_date is not None:
        from services.jira.jira_live_client import JiraLiveClient
        live = JiraLiveClient()
        date_status = 'CHECKED'
        try:
            local_issues = db.query(JiraKnowledge).limit(20).all()
        except Exception:
            local_issues = []
            date_status = 'UNAVAILABLE'
        code_text = message + ' ' + ' '.join(files)
        code_terms = _terms(code_text)
        for issue in local_issues:
            key = str(issue.jira_id or '').upper()
            if key in explicit:
                continue
            try:
                live_issue = live.get_issue(key)
            except (ValueError, RuntimeError, LookupError):
                date_status = 'PARTIAL_OR_UNAVAILABLE'
                continue
            created = _parse_date(live_issue.get('created_at'))
            if not created:
                continue
            days = abs((commit_date - created).total_seconds()) / 86400
            if days > window_days:
                continue
            jira_terms = _terms(' '.join([str(live_issue.get('summary') or ''),
                                         str(live_issue.get('description') or ''),
                                         str(issue.requirement or '')]))
            overlap = sorted(code_terms & jira_terms)
            if not overlap:
                continue  # Date proximity alone must not generate suggestions.
            relevance = len(overlap) / max(1, len(code_terms | jira_terms))
            candidates.append({'jira': key, 'title': live_issue.get('summary') or issue.title,
                               'status': 'POSSIBLE_RELATED_JIRA', 'days_apart': round(days, 2),
                               'matching_terms': overlap, 'relevance_score': round(relevance, 4),
                               'evidence': ['LIVE_JIRA_CREATED_DATE', 'CODE_REQUIREMENT_TERM_OVERLAP'],
                               'note': 'Suggested only; not an established commit-to-Jira link.'})
    candidates.sort(key=lambda c: (-c['relevance_score'], c['days_apart']))
    return {'status': 'FOUND', 'source': 'JIRA_GIT_CORRELATION', 'commit': full_sha,
            'commit_date': commit_date.isoformat() if commit_date else None,
            'changed_files': files, 'branch_refs': refs,
            'explicit_links': [{'jira': k, 'status': 'EXPLICIT_REFERENCE', 'evidence': v}
                               for k, v in sorted(explicit.items())],
            'possible_related_jiras': candidates[:3], 'window_days': window_days,
            'github_pr_status': pr_status, 'jira_date_lookup_status': date_status,
            'jira_development_panel_status': 'NOT_INTEGRATED',
            'note': 'Explicit references are source-backed; suggested JIRAs are not verified. '
                    'Branch names and PR text indicate references, not proof of implementation.'}
