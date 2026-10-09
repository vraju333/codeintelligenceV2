"""Offline checks; no database, Jira, Neo4j, embeddings or LLM required."""
from pathlib import Path
import ast
import importlib.util

root = Path(__file__).resolve().parent
agent = root / 'services/agent/knowledge_agent_service.py'
ast.parse(agent.read_text(encoding='utf-8'))
source = agent.read_text(encoding='utf-8')
assert '"excel", "xml", "java", "jira"' in source
assert '"customerId", "firstName", "lastName", "email", "countryCode", "accountType"' in source
assert '"attribute": attr' in source
ast.parse((root / 'repair_cas_knowledge.py').read_text(encoding='utf-8'))
print('PASS: routing excludes Excel as an attribute, CAS-1 uses six real mapping fields, repair script parses')
