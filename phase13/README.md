# CodeIntelligence Phase 13 — Validation pack

This **standalone validation harness** adds no changes to your working backend, Web UI or IntelliJ plugin. It checks the existing APIs and generates JSON/Markdown reports. It is **read-only by default**. It is not a production-ready complete Phase 13 certification; actual repository ground truth and live load/recovery testing are required.

## Use case

A business requirement changes `countryCode` handling: trim and uppercase input before validation. Verify that CodeIntelligence identifies affected Java methods, XML/JSON mapping transformations, relevant JIRA issues, tests, and baseline history. V1 and V3 mapping versions must not be confused. The code/method/JIRA/test IDs are **not fabricated**; add verified identifiers to `expected_country_code.json` after inspecting the project.

## Run (Windows PowerShell)

From the project root after starting FastAPI on port 8081:

```powershell
python phase13\validate.py --base http://127.0.0.1:8081 --project 'D:\AIlearning\customer-account-service' --output phase13-results
```

Read `phase13-results/report.md` and `report.json`. Default concurrency is 5 workers / 30 requests. Use `--requests 100 --workers 10` for a bounded heavier local check. No third-party Python packages are required.

## Test categories

- **API and project isolation**: mapping/test document endpoints, project-scoped records, imported mapping rows, requirement-impact endpoint.
- **Accuracy**: verify the `countryCode` answer against a **human-approved ground truth**. The draft expected file deliberately has `approved: false`; accuracy is **NOT_EVALUATED** until confirmed. The current simple term checks are smoke tests, not full Precision@K/Recall@K over ranked evidence IDs.
- **Scalability**: concurrent read requests, p50/p95 latency and HTTP failures. Default local p95 target: 2 seconds; tune to your machine.
- **Reliability**: repeated read stability. Optionally `--sync` runs enabled folder sources twice and **writes to PostgreSQL**; back up your database first.

## Exact end-to-end acceptance scenario

1. Confirm `countryCode` appears in relevant mapping rows and the correct project, not student mappings.
2. Inspect V1 and V3 transformations; confirm whether downstream `accountType` composition exists in V3.
3. Use the requirement-impact API and manually confirm the reported Java methods, JIRAs and tests against the indexed code and Knowledge Hub.
4. Record the verified expected identifiers in the JSON file; mark approved; rerun.
5. Check no cross-project evidence leaks into the result.
6. Run bounded concurrency and optional repeat synchronization; compare the generated reports.
7. Later repeat with large Java and Python repos and actual fault injection in a disposable environment.

**Caveat:** The existing backend may return a different shape for test documents or impact responses; any mismatch is reported as a failure for investigation, not silently suppressed. The script deliberately does not mutate code or DB schemas.
