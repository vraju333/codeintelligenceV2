from __future__ import annotations

import re
from typing import Any
from sqlalchemy.orm import Session

from services.scenario_rag.scenario_rag_registry_service import ScenarioRagRegistryService


class ScenarioRagEvaluationService:
    """Automated positive/negative regression evaluation for Scenario RAG."""

    def __init__(self):
        self.rag = ScenarioRagRegistryService()

    def run(self, db: Session, project_path: str, top_k: int = 8) -> dict[str, Any]:
        documents = self.rag._build_documents(db)
        cases = self._build_cases(documents)
        evaluated = []

        for case in cases:
            search = self.rag.search(
                db=db,
                query=case["query"],
                top_k=top_k,
            )
            results = search.get("results") or []
            passed, evidence = self._grade(case, results)
            evaluated.append({
                **case,
                "passed": passed,
                "match_count": len(results),
                "actual_evidence": evidence,
            })

        passed = sum(1 for x in evaluated if x["passed"])
        total = len(evaluated)
        positives = [x for x in evaluated if x["case_type"] == "POSITIVE"]
        negatives = [x for x in evaluated if x["case_type"] == "NEGATIVE"]

        return {
            "status": "PASS" if passed == total else "FAIL",
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "accuracy_percent": round((passed / total) * 100, 2) if total else 100.0,
            "positive": {
                "total": len(positives),
                "passed": sum(1 for x in positives if x["passed"]),
            },
            "negative": {
                "total": len(negatives),
                "passed": sum(1 for x in negatives if x["passed"]),
            },
            "cases": evaluated,
        }

    def _build_cases(self, documents: list) -> list[dict]:
        cases, seen = [], set()

        for doc in documents:
            meta = dict(getattr(doc, "metadata", {}) or {})
            if str(meta.get("document_type") or "").lower() != "test_baseline":
                continue

            test_name = str(meta.get("relevant_test_baseline") or "").strip()
            jira_ids = [str(x).strip().upper() for x in (meta.get("jira_ids") or []) if str(x).strip()]
            if not test_name or not jira_ids:
                continue

            domain = self._domain(test_name)
            for jira_id in jira_ids:
                # JIRA id + test baseline are deterministic captured evidence.
                query = f"Which JIRA is linked to {test_name}?"
                key = (query.lower(), jira_id, test_name.lower())
                if key in seen:
                    continue
                seen.add(key)
                cases.append({
                    "case_type": "POSITIVE",
                    "query": query,
                    "expected_jira": jira_id,
                    "expected_test_baseline": test_name,
                    "expected_domain": domain,
                })

        # Known-absent concepts protect against broad semantic false positives.
        for query in [
            "Which release tested student scholarship eligibility?",
            "Which JIRA changed customer loyalty reward points?",
            "Where was employee cryptocurrency wallet validation changed?",
        ]:
            cases.append({
                "case_type": "NEGATIVE",
                "query": query,
                "expected_jira": None,
                "expected_test_baseline": None,
                "expected_domain": None,
            })

        return cases

    def _grade(self, case: dict, results: list[dict]) -> tuple[bool, list[dict]]:
        evidence = []
        for item in results:
            meta = item.get("metadata") or {}
            evidence.append({
                "scenario_code": meta.get("scenario_code"),
                "test_baseline": meta.get("relevant_test_baseline"),
                "jira_ids": list(meta.get("jira_ids") or []),
                "release_name": meta.get("release_name"),
                "score": item.get("score"),
            })

        if case["case_type"] == "NEGATIVE":
            return len(results) == 0, evidence[:5]

        expected_jira = str(case["expected_jira"]).upper()
        expected_test = str(case["expected_test_baseline"]).lower()
        for row in evidence:
            jira_ids = {str(x).upper() for x in (row.get("jira_ids") or [])}
            test_name = str(row.get("test_baseline") or "").lower()
            if expected_jira in jira_ids and expected_test == test_name:
                return True, evidence[:5]
        return False, evidence[:5]

    @staticmethod
    def _domain(test_name: str) -> str | None:
        upper = str(test_name or "").upper()
        for domain in ("STUDENT", "EMPLOYEE", "CUSTOMER"):
            if domain in upper:
                return domain
        return None
