"""Dependency-free CAS-1 routing regression checks (not live Neo4j verification)."""
import ast
from pathlib import Path
p = Path(__file__).parent / 'services/agent/knowledge_agent_service.py'
s = p.read_text(encoding='utf-8')
ast.parse(s)
assert 'customer_six_requested' in s
assert '"mapping_lineage", "args": {"attribute": attr}' in s
assert '"mapping_graph_lineage", "args": {"attribute": attr}' in s
assert '"customerId", "firstName", "lastName", "email", "countryCode", "accountType"' in s
assert 'not customer_six_requested' in s
assert '"excel", "xml", "java", "jira"' in s
print('PASS: syntax and CAS-1 six-attribute mapping routing checks')
print('NOT VERIFIED: runtime tool execution, persisted mapping rows, Neo4j relationships, Jira linkage')
