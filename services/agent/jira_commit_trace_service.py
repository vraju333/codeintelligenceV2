"""Deterministic, read-only JIRA-key -> Git commit -> Java source evidence.

No inferred JIRA link, endpoint, or test execution is promoted to verified evidence.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from config import settings


class JiraCommitTraceService:
    def __init__(self, project_path: str | None = None):
        self.root = Path(project_path or settings.JAVA_PROJECT_PATH or '').expanduser()

    def git(self, *args: str) -> str:
        proc = subprocess.run(['git', '-C', str(self.root), *args], text=True,
                              capture_output=True, timeout=25, check=False)
        if proc.returncode:
            raise ValueError('Git evidence unavailable: ' + proc.stderr.strip()[:350])
        return proc.stdout

    @staticmethod
    def methods(source: str) -> dict[str, tuple[int, int, str]]:
        """Find ordinary Java method declarations and their balanced bodies.

        Conservative: unresolved methods are omitted rather than fabricated.
        """
        result = {}
        pattern = re.compile(r'(?m)^\s*(?:@[\w.]+(?:\([^\n]*\))?\s*)*(?:(?:public|private|protected|static|final|synchronized|abstract|native)\s+)*(?:[\w<>\[\],.?]+\s+)+(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{')
        for match in pattern.finditer(source):
            name = match.group(1)
            if name in ('if', 'for', 'while', 'switch', 'catch', 'return'):
                continue
            start = match.end() - 1
            depth = 0
            end = None
            for i in range(start, len(source)):
                if source[i] == '{':
                    depth += 1
                elif source[i] == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end is not None:
                result[name] = (source.count('\n', 0, match.start()) + 1,
                                source.count('\n', 0, end) + 1, source[match.start():end])
        return result

    def analyse(self, jira: str) -> dict:
        jira = jira.upper().strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*-\d+', jira):
            raise ValueError('Invalid JIRA issue key')
        if not self.root.is_dir():
            return {'jira': jira, 'status': 'PROJECT_UNAVAILABLE', 'commits': []}
        # Search full reachable local history; only an explicit commit-message key is a verified link.
        log = self.git('log', '--all', '--format=%H%x09%B%x00', '--regexp-ignore-case', '--fixed-strings', '--grep', jira)
        matches = []
        for record in log.split('\x00'):
            record = record.strip()
            if '\t' not in record:
                continue
            sha, message = record.split('\t', 1)
            if not re.fullmatch(r'[0-9a-f]{40}', sha.strip()):
                continue
            # The issue key may appear in the subject OR commit body.
            if re.search(r'(?<![A-Z0-9_])' + re.escape(jira) + r'(?![A-Z0-9_\-])', message, re.I):
                matches.append((sha.strip(), message.splitlines()[0].strip()))
        commits = []
        for sha, subject in matches[:50]:
            parent_line = self.git('rev-list', '--parents', '-n', '1', sha).strip().split()
            parent = parent_line[1] if len(parent_line) > 1 else None
            changed = self.git('diff-tree', '--root', '--no-commit-id', '--name-only', '-r', sha).splitlines()
            java = []
            for filename in changed:
                if not filename.endswith('.java'):
                    continue
                new = self.git('show', f'{sha}:{filename}')
                old = ''
                if parent:
                    check = subprocess.run(['git', '-C', str(self.root), 'cat-file', '-e', f'{parent}:{filename}'], capture_output=True, timeout=10)
                    if check.returncode == 0:
                        old = self.git('show', f'{parent}:{filename}')
                diff = self.git('diff', '--unified=0', parent or '4b825dc642cb6eb9a060e54bf8d69288fbee4904', sha, '--', filename) if parent else ''
                old_methods, new_methods = self.methods(old), self.methods(new)
                changed_methods = []
                from services.agent.commit_call_path_service import tokens
                for method in sorted(set(old_methods) | set(new_methods)):
                    before = old_methods.get(method)
                    after = new_methods.get(method)
                    if tokens(before[2] if before else '') != tokens(after[2] if after else ''):
                        changed_methods.append({'method': method, 'change': 'MODIFIED' if before and after else ('ADDED' if after else 'REMOVED'), 'line': after[0] if after else None})
                java.append({'file': filename, 'is_test': '/src/test/' in '/' + filename.replace('\\', '/'), 'changed_methods': changed_methods,
                             'diff_lines': [x for x in diff.splitlines() if x.startswith(('+', '-')) and not x.startswith(('+++', '---'))][:30]})
            commits.append({'sha': sha, 'subject': subject, 'parent': parent, 'java_files': java, 'changed_files': changed})
        return {'jira': jira, 'status': 'MATCHED_COMMIT_MESSAGE' if commits else 'NO_EXPLICIT_COMMIT_LINK', 'commits': commits,
                'link_rule': 'Exact JIRA issue key in Git commit subject or body; no inferred association', 'endpoint_proof': 'NOT_RESOLVED_BY_GIT_DIFF'}


def format_trace(evidence: dict) -> str:
    lines = ['GIT → JIRA COMMIT EVIDENCE', 'JIRA: ' + evidence['jira'], 'Association: ' + evidence['status']]
    for commit in evidence.get('commits', []):
        lines.extend(['Commit: ' + commit['sha'], 'Subject: ' + commit['subject'], 'Parent: ' + str(commit['parent'] or '(root commit)')])
        for item in commit['java_files']:
            lines.append('Java file: ' + item['file'] + (' [test]' if item['is_test'] else ' [production]'))
            if not item['changed_methods']:
                lines.append('  Changed methods: unresolved by conservative source parser; inspect diff')
            for method in item['changed_methods']:
                lines.append('  ' + method['change'] + ' method: ' + method['method'] + ' (new line ' + str(method['line']) + ')')
            for diff in item['diff_lines'][:12]:
                lines.append('    ' + diff)
    lines.append('Controller call paths are evaluated separately from commit evidence; see COMMIT-SCOPED SOURCE CALL PATHS below.')
    lines.append('QA execution status: NOT_VERIFIED; importing test plans is not a test run.')
    return '\n'.join(lines)
