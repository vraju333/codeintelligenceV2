"""Read-only commit metadata and safe provider links for already-associated Git SHAs."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, urlsplit

SHA = re.compile(r'^[0-9a-fA-F]{40}$')

def _git(project: str, *args: str) -> str | None:
    if not project or not Path(project).is_dir():
        return None
    try:
        result = subprocess.run(['git', '-C', str(project), *args], capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=12, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None

def commit_url(remote: str | None, sha: str) -> str | None:
    """Accept known hosting providers only; never render arbitrary remote URLs as links."""
    if not remote or not SHA.fullmatch(sha):
        return None
    remote = remote.strip()
    match = re.fullmatch(r'git@([\w.-]+):([^\s]+)', remote)
    if match:
        host, path = match.groups()
    else:
        parsed = urlsplit(remote)
        if parsed.scheme not in ('https', 'ssh') or not parsed.hostname or parsed.port or parsed.query or parsed.fragment or parsed.username or parsed.password:
            return None
        host, path = parsed.hostname, parsed.path.lstrip('/')
    host = host.lower()
    if host not in ('github.com', 'gitlab.com', 'bitbucket.org'):
        return None
    path = path.removesuffix('.git').strip('/')
    segments = path.split('/')
    if len(segments) < 2 or any(not re.fullmatch(r'[\w.-]+', segment) or segment in ('.', '..') for segment in segments):
        return None
    if host in ('github.com', 'bitbucket.org') and len(segments) != 2:
        return None
    suffix = '/-/commit/' if host == 'gitlab.com' else '/commits/' if host == 'bitbucket.org' else '/commit/'
    return 'https://' + host + '/' + '/'.join(quote(s, safe='') for s in segments) + suffix + sha.lower()

def enrich_commits(project: str, commits: list[dict]) -> list[dict]:
    """Preserve all supplied verified associations; deduplicate by SHA, no guessed commits."""
    remote = _git(project, 'remote', 'get-url', 'origin')
    output, seen = [], set()
    for commit in commits:
        sha = str(commit.get('sha') or '').lower()
        if not SHA.fullmatch(sha) or sha in seen:
            continue
        seen.add(sha)
        row = {'sha': sha, 'short_sha': sha[:7], 'subject': commit.get('subject'),
               'association': 'MATCHED_COMMIT_MESSAGE', 'url': commit_url(remote, sha),
               'author': None, 'author_date': None, 'committer': None, 'committed_at': None,
               'changed_files': commit.get('changed_files') or []}
        metadata = _git(project, 'show', '-s', '--format=%an%x00%aI%x00%cn%x00%cI%x00%s', sha)
        if metadata:
            parts = metadata.split('\x00', 4)
            if len(parts) == 5:
                row.update(author=parts[0], author_date=parts[1], committer=parts[2],
                           committed_at=parts[3], subject=parts[4])
        output.append(row)
    return output

def format_commit_history(commits: list[dict]) -> list[str]:
    lines = [f'Historical Git commits: {len(commits)} matched']
    for c in commits:
        lines.append(f"- {c['short_sha']} — {c.get('subject') or '(subject unavailable)'}")
        if c.get('author'):
            lines.append(f"  Author: {c['author']} | Authored: {c['author_date']}")
        if c.get('committer'):
            lines.append(f"  Committer: {c['committer']} | Committed: {c['committed_at']}")
        if c.get('url'):
            lines.append(f"  Commit link: {c['url']}")
        if c.get('changed_files'):
            lines.append('  Changed files: ' + ', '.join(c['changed_files']))
    return lines
