"""Offline safety checks. Live database, Jira and Neo4j require local integration testing."""
import ast
from pathlib import Path
root = Path(__file__).resolve().parent
files = [
    'repositories/scenario_repository.py',
    'services/lineage/attribute_impact_service.py',
    'services/lineage/mapping_intelligence_service.py',
    'services/agent/knowledge_tool_registry.py',
]
for name in files:
    ast.parse((root / name).read_text(encoding='utf-8'))
repo = (root / files[0]).read_text()
impact = (root / files[1]).read_text()
map_service = (root / files[2]).read_text()
registry = (root / files[3]).read_text()
assert '_same_project(row.project_path, settings.JAVA_PROJECT_PATH)' in repo
assert 'self.scenarios.get_all_for_active_project(db)' in impact
assert 'ensure_active_project_workbooks' in map_service
assert '_active_project_rows(db)' in map_service
assert 'service._canonical_project_key(doc.project_path)' in registry
assert 'service.ensure_active_project_workbooks(self.db)' in registry
print('PASS: 4 files compile and dynamic project/mapping safety checks')
print('NOT VERIFIED: live Jira, workbook import, Neo4j and end-to-end runtime')
