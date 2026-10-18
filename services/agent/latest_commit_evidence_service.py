"""Evidence-only analysis of HEAD against its first parent; never examines dirty files."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from config import settings


class LatestCommitEvidenceService:
    def __init__(self, project_path: str | None = None):
        self.project_path = Path(project_path or settings.JAVA_PROJECT_PATH or '').expanduser()

    def _git(self, *args: str) -> str:
        result = subprocess.run(['git', '-C', str(self.project_path), *args],
                                capture_output=True, text=True, timeout=20, check=False)
        if result.returncode:
            raise ValueError(f'Git command failed: {" ".join(args[:3])}: {result.stderr.strip()}')
        return result.stdout

    def analyse(self) -> dict:
        if not self.project_path.is_dir():
            raise ValueError('Configured Java project directory is unavailable')
        sha = self._git('rev-parse', 'HEAD').strip()
        subject = self._git('log', '-1', '--format=%s').strip()
        parents = self._git('rev-list', '--parents', '-n', '1', 'HEAD').split()
        base = parents[1] if len(parents) > 1 else '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
        jira = sorted(set(re.findall(r'\b[A-Z][A-Z0-9_]+-\d+\b', subject, re.I)))
        names = self._git('diff', '--name-only', base, sha, '--').splitlines()
        files = []
        for name in names:
            if not name.lower().endswith('.java'):
                continue
            diff = self._git('diff', '--unified=3', base, sha, '--', name)
            before = self._git('show', f'{base}:{name}') if self._exists(base, name) else ''
            after = self._git('show', f'{sha}:{name}') if self._exists(sha, name) else ''
            old_regex = re.findall(r'Pattern\.compile\(\s*"([^"\n]+)"\s*\)', before)
            new_regex = re.findall(r'Pattern\.compile\(\s*"([^"\n]+)"\s*\)', after)
            changed_patterns = [{'before': x, 'after': y} for x, y in zip(old_regex, new_regex) if x != y]
            test_methods = re.findall(r'@Test\s+(?:public\s+|protected\s+|private\s+)?(?:void|[A-Za-z_][\w<>]*)\s+(\w+)\s*\(', after)
            files.append({'path': name, 'class_name': Path(name).stem,
                          'is_test': '/src/test/' in '/' + name.replace('\\', '/'),
                          'changed_lines': [s for s in diff.splitlines() if s.startswith(('+', '-')) and not s.startswith(('+++', '---'))][:80],
                          'pattern_changes': changed_patterns, 'test_methods': test_methods})
        return {'analysis_mode': 'LATEST_COMMIT', 'comparison': f'{base[:12]}..{sha[:12]}',
                'commit': sha, 'subject': subject, 'explicit_jira_references': jira,
                'changed_java_files': files, 'changed_file_count': len(names),
                'dirty_worktree_ignored': True,
                'coverage_note': 'JUnit methods are available test candidates; endpoint coverage is not inferred from class membership.',
                'source': 'local Git HEAD vs first parent'}

    def _exists(self, revision: str, name: str) -> bool:
        result = subprocess.run(['git', '-C', str(self.project_path), 'cat-file', '-e', f'{revision}:{name}'],
                                capture_output=True, timeout=10, check=False)
        return result.returncode == 0
