from __future__ import annotations

import re
import time
from typing import Any
from sqlalchemy.orm import Session

from services.scenario_rag.scenario_rag_registry_service import ScenarioRagRegistryService
from repositories.scenario_baseline_repository import ScenarioBaselineRepository
from services.scenario.scenario_service import ScenarioService


class ScenarioRagEvaluationService:
    """Automated positive/negative regression evaluation for Scenario RAG."""

    def __init__(self):
        self.rag = ScenarioRagRegistryService()
        self.baseline_repository = ScenarioBaselineRepository()

    def run(self, db: Session, project_path: str, top_k: int = 8) -> dict[str, Any]:
        # Evaluate the exact same Scenario RAG corpus that search() uses.
        # Previously this called _build_documents(db) directly. That can return
        # a different project-scoped set (or only legacy scenarios) than the
        # already-built active RAG index, which caused 0 positive tests even
        # though test-baseline/JIRA evidence existed in the registry index.
        active_project = project_path or self.rag._active_project_path()
        documents = self.rag._documents_by_project.get(active_project)
        if documents is None:
            documents = self.rag._load_documents(active_project)
        if documents is None:
            self.rag.rebuild_index(db)
            documents = self.rag._documents_by_project.get(active_project, [])

        # Ground-truth evaluation cases must come from PostgreSQL/SQLite metadata,
        # not from the RAG index being evaluated. Otherwise a stale/missing index
        # can incorrectly produce zero positive tests.
        cases = self._build_cases_from_db(db)
        evaluated = []

        for case in cases:
            started = time.perf_counter()
            search = self.rag.search(
                db=db,
                query=case["query"],
                top_k=top_k,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            results = search.get("results") or []
            passed, evidence = self._grade(case, results)
            metrics = self._case_metrics(case, results, top_k=top_k)
            evaluated.append({
                **case,
                "passed": passed,
                "match_count": len(results),
                "latency_ms": round(latency_ms, 2),
                **metrics,
                "actual_evidence": evidence,
            })

        passed = sum(1 for x in evaluated if x["passed"])
        total = len(evaluated)
        positives = [x for x in evaluated if x["case_type"] == "POSITIVE"]
        negatives = [x for x in evaluated if x["case_type"] == "NEGATIVE"]

        def avg(items: list[dict], key: str) -> float:
            return round(sum(float(x.get(key) or 0.0) for x in items) / len(items), 4) if items else 0.0

        precision = avg(evaluated, "precision_at_k")
        recall = avg(positives, "recall_at_k")
        mrr = avg(positives, "reciprocal_rank")
        hit = avg(positives, "hit_at_k")
        avg_latency = avg(evaluated, "latency_ms")
        irrelevant = sum(int(x.get("irrelevant_result_count") or 0) for x in evaluated)

        return {
            "status": "PASS" if passed == total else "FAIL",
            "top_k": max(1, min(int(top_k or 8), 10)),
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "accuracy_percent": round((passed / total) * 100, 2) if total else 100.0,
            "precision_at_k": precision,
            "recall_at_k": recall,
            "mrr": mrr,
            "hit_at_k": hit,
            "hit_at_k_percent": round(hit * 100, 2),
            "irrelevant_result_count": irrelevant,
            "average_latency_ms": avg_latency,
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


    def _build_cases_from_db(self, db: Session) -> list[dict]:
        """Build dynamic ground-truth cases from captured Scenario Registry data.

        This intentionally does NOT inspect the RAG index. The database is the
        source of truth; RAG is the system under test.
        """
        cases: list[dict] = []
        seen: set[tuple[str, str, str]] = set()

        scenarios = ScenarioService().get_all_for_active_project(db)
        for scenario in scenarios:
            tests = self.baseline_repository.find_test_baselines(db, scenario.id)
            for test in tests:
                test_name = str(getattr(test, "baseline_name", "") or "").strip()
                jira_ids = self.rag._normalize_list(getattr(test, "jira_ids", None))
                if not test_name or not jira_ids:
                    continue

                for jira_id in jira_ids:
                    jira_id = str(jira_id).strip().upper()
                    if not jira_id:
                        continue
                    query = f"Which JIRA is linked to {test_name}?"
                    key = (query.casefold(), jira_id, test_name.casefold())
                    if key in seen:
                        continue
                    seen.add(key)
                    cases.append({
                        "case_type": "POSITIVE",
                        "query": query,
                        "expected_jira": jira_id,
                        "expected_test_baseline": test_name,
                        "expected_domain": self._domain(test_name),
                        "expected_scenario": getattr(scenario, "scenario_code", None),
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

    def _case_metrics(self, case: dict, results: list[dict], top_k: int) -> dict[str, Any]:
        k = max(1, min(int(top_k or 8), 10))
        ranked = list(results[:k])

        if case["case_type"] == "NEGATIVE":
            # For a known-absent query every returned item is noise. A clean
            # empty result therefore has perfect precision for this regression case.
            irrelevant = len(ranked)
            return {
                "precision_at_k": 1.0 if irrelevant == 0 else 0.0,
                "recall_at_k": None,
                "reciprocal_rank": None,
                "hit_at_k": None,
                "irrelevant_result_count": irrelevant,
                "first_relevant_rank": None,
            }

        relevant_flags = [self._is_expected_result(case, item) for item in ranked]
        relevant_count = sum(1 for flag in relevant_flags if flag)
        first_rank = next((i + 1 for i, flag in enumerate(relevant_flags) if flag), None)

        # Each generated positive case has one deterministic expected evidence
        # pair: expected JIRA + expected test baseline. Therefore Recall@K is
        # 1 when that evidence is retrieved and 0 otherwise.
        return {
            "precision_at_k": round(relevant_count / len(ranked), 4) if ranked else 0.0,
            "recall_at_k": 1.0 if relevant_count else 0.0,
            "reciprocal_rank": round(1.0 / first_rank, 4) if first_rank else 0.0,
            "hit_at_k": 1.0 if relevant_count else 0.0,
            "irrelevant_result_count": len(ranked) - relevant_count,
            "first_relevant_rank": first_rank,
        }

    @staticmethod
    def _is_expected_result(case: dict, item: dict) -> bool:
        meta = item.get("metadata") or {}
        expected_jira = str(case.get("expected_jira") or "").upper()
        expected_test = str(case.get("expected_test_baseline") or "").lower()
        jira_ids = {str(x).upper() for x in (meta.get("jira_ids") or [])}
        test_name = str(meta.get("relevant_test_baseline") or "").lower()
        return bool(expected_jira and expected_test and expected_jira in jira_ids and expected_test == test_name)

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
