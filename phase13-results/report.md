# CodeIntelligence Phase 13 results

Project: `D:\AIlearning\customer-account-service`

| Check | Result | Details |
|---|---|---|
| API mapping_documents | PASS | HTTP 200; 190.68 ms;  |
| API test_documents | PASS | HTTP 200; 18.01 ms;  |
| API knowledge_sources | PASS | HTTP 200; 45.95 ms;  |
| API impact | PASS | HTTP 200; 251.24 ms;  |
| mapping project isolation | PASS | 9 mapping records |
| mapping documents present | PASS | 9 records; 8 unique filenames |
| mapping row evidence | PASS | At least one imported mapping has rows |
| test documents returned | PASS | keys: ['project_path', 'documents', 'record_count', 'distinct_file_count', 'sync'] |
| impact response structure | PASS | keys: ['status', 'mode', 'input', 'project', 'jira', 'attributes', 'historical_jiras', 'mappings', 'mapping_documents', 'scenarios', 'code_impact', 'java_evidence', 'affected_operations', 'operation_evidence', 'git_commi |
| ground truth approved | NOT_EVALUATED | Expected evidence is a draft. Verify actual code/JIRA/test IDs and set approved=true. |
| concurrent API reliability | PASS | 30/30 HTTP successes |
| mapping API p95 <= 2000ms | PASS | p95=116.62ms (local development target, adjust for hardware) |
| read-only result stability | PASS | 9 records before, 9 after |

## Load test
Concurrent requests: 30; workers: 5; p50: 24.735 ms; p95: 116.62 ms; failures: 0

No results have been independently verified against the local Java repository.
