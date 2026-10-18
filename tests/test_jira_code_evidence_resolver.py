import importlib.util
import subprocess
import sys
import types
from pathlib import Path

sys.modules.setdefault("db_models", types.SimpleNamespace(JiraKnowledge=type("JiraKnowledge", (), {})))
spec = importlib.util.spec_from_file_location("resolver", Path(__file__).resolve().parents[1] / "services/jira/jira_code_evidence_resolver.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class Row:
    jira_id = "CAS-2"
    title = "Update Country Code"
    requirement = "Country Code must use two letters"
    project_path = None

class Foreign:
    jira_id = "STUD-202"
    title = "Update Country Code"
    requirement = "Country Code"
    project_path = "/another/project"

class Query:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows

class DB:
    def __init__(self, rows): self.rows = rows
    def query(self, _): return Query(self.rows)

def test_aliases():
    assert mod._words("countryCode") == {"country", "code"}
    assert mod._words("Country Code") == {"country", "code"}

def test_no_commit_and_isolation(tmp_path):
    (tmp_path / "CustomerService.java").write_text('class CustomerService {\n private void validate() { String countryCode = "US"; }\n}\n')
    row = Row(); row.project_path = str(tmp_path)
    result = mod.resolve_jira_code_candidates(DB([row, Foreign()]), str(tmp_path))
    assert result["mode"] == "LIVE_SOURCE"
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["classification"] == "INFERRED"
    assert result["candidates"][0]["score_kind"] == "HEURISTIC_RELEVANCE_NOT_PROBABILITY"
    assert "STUD-202" not in str(result)

def test_no_match(tmp_path):
    (tmp_path / "Service.java").write_text('class Service {\n public void validate() { String accountType = "BASIC"; }\n}\n')
    row = Row(); row.project_path = str(tmp_path)
    result = mod.resolve_jira_code_candidates(DB([row]), str(tmp_path))
    assert result["candidate_count"] == 0

def test_explicit_requires_trusted_link(tmp_path):
    row = Row(); row.project_path = str(tmp_path)
    bad = [{"jira_id": "CAS-2", "method": "validate", "verified": True, "evidence_id": "E1", "source_type": "REVIEWED_ASSOCIATION", "project_path": "/wrong"}]
    assert not mod.resolve_jira_code_candidates(DB([row]), str(tmp_path), explicit_links=bad)["verified_associations"]
    good = [{**bad[0], "project_path": str(tmp_path)}]
    result = mod.resolve_jira_code_candidates(DB([row]), str(tmp_path), explicit_links=good)
    assert result["verified_associations"][0]["classification"] == "VERIFIED"

def test_git_mode(tmp_path):
    def git(*args): return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q"); git("config", "user.email", "test@example.com"); git("config", "user.name", "Test")
    f = tmp_path / "CustomerService.java"
    f.write_text('class CustomerService {\n private void validate() { int x = 1; }\n}\n')
    git("add", "."); git("commit", "-qm", "initial"); base = git("rev-parse", "HEAD")
    f.write_text('class CustomerService {\n private void validate() { String countryCode = "US"; }\n}\n')
    git("add", "."); git("commit", "-qm", "change")
    row = Row(); row.project_path = str(tmp_path)
    result = mod.resolve_jira_code_candidates(DB([row]), str(tmp_path), base)
    assert result["mode"] == "GIT_DIFF"
    assert result["candidate_count"] == 1
