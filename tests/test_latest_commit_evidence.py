import subprocess
from pathlib import Path
from services.agent.latest_commit_evidence_service import LatestCommitEvidenceService


def test_latest_commit_ignores_dirty_tree(tmp_path):
    def git(*args):
        return subprocess.run(['git', '-C', str(tmp_path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    git('init', '-q')
    git('config', 'user.name', 'Tester')
    git('config', 'user.email', 'test@example.com')
    path = tmp_path / 'src/main/java/CustomerService.java'
    path.parent.mkdir(parents=True)
    path.write_text('class CustomerService { String rule = Pattern.compile("^[A-Z]{2,3}$").toString(); }')
    git('add', '.')
    git('commit', '-qm', 'initial')
    path.write_text('class CustomerService { String rule = Pattern.compile("^[A-Z]{2}$").toString(); }')
    git('add', '.')
    git('commit', '-qm', 'CAS-2: country code')
    path.write_text('// dirty working tree, should not appear in latest commit evidence\n' + path.read_text())
    evidence = LatestCommitEvidenceService(str(tmp_path)).analyse()
    assert evidence['explicit_jira_references'] == ['CAS-2']
    assert evidence['changed_file_count'] == 1
    assert evidence['changed_java_files'][0]['pattern_changes'] == [{'before': '^[A-Z]{2,3}$', 'after': '^[A-Z]{2}$'}]
    assert evidence['dirty_worktree_ignored'] is True
