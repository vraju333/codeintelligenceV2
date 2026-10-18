from unittest.mock import patch
from services.agent.git_evidence_enrichment import commit_url, enrich_commits, format_commit_history

SHA1 = 'a' * 40
SHA2 = 'b' * 40

def test_supported_remotes():
    assert commit_url('git@github.com:org/repo.git', SHA1) == 'https://github.com/org/repo/commit/' + SHA1
    assert commit_url('https://gitlab.com/org/repo.git', SHA1) == 'https://gitlab.com/org/repo/-/commit/' + SHA1
    assert commit_url('https://bitbucket.org/org/repo.git', SHA1) == 'https://bitbucket.org/org/repo/commits/' + SHA1

def test_reject_unsafe_links():
    for remote in ('file:///etc/passwd', 'https://evil.example/repo/x', 'https://github.com/org/../repo', 'https://github.com/org/repo?x=1'):
        assert commit_url(remote, SHA1) is None
    assert commit_url('https://github.com/org/repo', 'not-a-sha') is None

def test_multiple_commits_and_deduplication(tmp_path):
    with patch('services.agent.git_evidence_enrichment._git', side_effect=lambda p, *args: 'git@github.com:org/repo.git' if args[0] == 'remote' else 'Dev\x002026-10-18T10:00:00+05:30\x00Dev\x002026-10-18T11:00:00+05:30\x00Commit title'):
        result = enrich_commits(str(tmp_path), [{'sha': SHA1}, {'sha': SHA2}, {'sha': SHA1}])
    assert len(result) == 2
    assert all(c['url'] and c['committed_at'] for c in result)
    assert 'Historical Git commits: 2 matched' in format_commit_history(result)
