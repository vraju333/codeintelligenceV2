"""Regression checks for committed and uncommitted Java Git comparisons."""
import subprocess
import pytest
from services.regression.git_diff_service import GitDiffService
from config import settings


def git(root, *args):
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'CI Test')
    git(tmp_path, 'config', 'user.email', 'ci@example.com')
    java = tmp_path / 'Example.java'
    java.write_text('public class Example {\n public String map(String x) { return x.trim(); }\n}\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'baseline')
    original = git(tmp_path, 'rev-parse', 'HEAD')
    java.write_text('public class Example {\n public String map(String x) { return x; }\n}\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'remove trim')
    current = git(tmp_path, 'rev-parse', 'HEAD')
    monkeypatch.setattr(settings, 'JAVA_PROJECT_PATH', str(tmp_path))
    return tmp_path, java, original, current


def test_last_commit_detects_committed_method_change(repository):
    _, _, _, current = repository
    result = GitDiffService().analyse_changes(mode='last_commit')
    assert result['total_changed_java_files'] == 1
    assert result['changed_files'][0]['semantic_analysis']['functional_methods'] == ['map']
    assert result['comparison']['target_ref'] == current


def test_explicit_revision_comparison(repository):
    _, _, old, current = repository
    result = GitDiffService().analyse_changes(mode='revisions', base_ref=old, target_ref=current)
    assert result['total_changed_java_files'] == 1
    assert result['changed_files'][0]['changed_methods'][0]['method_name'] == 'map'


def test_clean_worktree_returns_zero(repository):
    result = GitDiffService().analyse_changes()
    assert result['total_changed_java_files'] == 0


def test_worktree_detects_uncommitted_change(repository):
    _, java, _, _ = repository
    java.write_text('public class Example {\n public String map(String x) { return x.toUpperCase(); }\n}\n')
    result = GitDiffService().analyse_changes()
    assert result['total_changed_java_files'] == 1


def test_invalid_ref_reports_error(repository):
    with pytest.raises(RuntimeError):
        GitDiffService().analyse_changes(mode='revisions', base_ref='DOES_NOT_EXIST', target_ref='HEAD')
