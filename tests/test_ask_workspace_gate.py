"""Focused tests: run with `pytest -q tests/test_ask_workspace_gate.py`."""
from datetime import datetime
from types import SimpleNamespace
import sys
import types

from services.agent.ask_workspace_gate import (
    canonical, is_connector_inventory, connector_inventory, foreign_jira_guard,
)

class Column:
    def in_(self, values):
        return values

class JiraKnowledge:
    jira_id = Column()

class KnowledgeSyncSource:
    id = Column()

class Query:
    def __init__(self, rows): self.rows = rows
    def filter(self, *_): return self
    def order_by(self, *_): return self
    def all(self): return self.rows

class DB:
    def __init__(self, jiras=(), sources=()): self.jiras = jiras; self.sources = sources
    def query(self, cls): return Query(self.jiras if cls is JiraKnowledge else self.sources)

def setup_module():
    module = types.ModuleType('db_models')
    module.JiraKnowledge = JiraKnowledge
    module.KnowledgeSyncSource = KnowledgeSyncSource
    sys.modules['db_models'] = module

def test_connector_intent():
    assert is_connector_inventory('knowledge hub connections please')
    assert is_connector_inventory('list saved Knowledge Hub connections and their sync status')
    assert not is_connector_inventory('run sync now')
    assert not is_connector_inventory('find code dependencies and connections')

def test_workspace_isolation():
    db = DB(jiras=[SimpleNamespace(jira_id='CAS-2', project_path=r'D:\AIlearning\customer-account-service')])
    blocked = foreign_jira_guard(db, 'CAS-2 related JIRA requirement information please', r'D:\AIlearning\spring-batch')
    assert blocked['status'] == 'NOT_FOUND'
    assert 'Customer Account Service' not in blocked['answer']
    assert foreign_jira_guard(db, 'CAS-2 requirement', r'd:\ailearning\customer-account-service') is None
    assert foreign_jira_guard(db, 'CAS-2 requirement', None)['status'] == 'PROJECT_REQUIRED'

def test_saved_sources_are_scoped_and_no_secrets():
    db = DB(sources=[
        SimpleNamespace(id=1, name='Jira CAS', source_type='JIRA', enabled='true',
                        last_status='SUCCESS', last_sync_at=datetime(2026, 11, 10),
                        last_error=None, project_path=r'D:\AIlearning\customer-account-service',
                        config_json='{"api_token":"secret"}'),
        SimpleNamespace(id=2, name='Batch docs', source_type='DOCUMENT_FOLDER', enabled='false',
                        last_status=None, last_sync_at=None, last_error=None,
                        project_path=r'D:\AIlearning\spring-batch', config_json='{}')])
    result = connector_inventory(db, 'knowledge hub connections please', r'D:\AIlearning\spring-batch')
    assert result['status'] == 'FOUND'
    assert len(result['connections']) == 1
    assert result['connections'][0]['name'] == 'Batch docs'
    assert 'secret' not in str(result)
    assert connector_inventory(db, 'connections', None)['status'] == 'PROJECT_REQUIRED'
