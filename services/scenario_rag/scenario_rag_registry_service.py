from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from baseline_models import ScenarioBaseline
from config import settings
from db_models import JiraKnowledge
from repositories.scenario_baseline_repository import ScenarioBaselineRepository
from services.scenario.scenario_service import ScenarioService
from services.jira.requirement_llm_service import RequirementLlmService


@dataclass
class ScenarioRegistryDocument:
    document_id: str
    scenario_id: int
    scenario_code: str
    title: str
    text: str
    metadata: dict[str, Any]


class ScenarioRagRegistryService:
    """Hybrid Scenario RAG.

    Retrieval combines:
      1. exact/symbol evidence,
      2. BM25 lexical ranking,
      3. MiniLM embeddings stored in FAISS.

    Release and test-baseline knowledge are indexed as separate documents so a
    GPA query can retrieve STUDENT_UPDATE without also presenting EMPLOYEE_UPDATE.
    """

    STOP_WORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
        "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "the",
        "this", "to", "was", "were", "what", "when", "where", "which", "who",
        "with", "change", "changed", "update", "updated", "read", "scenario",
        "release", "version", "data", "test", "tested",
    }

    def __init__(self):
        self.baseline_repository = ScenarioBaselineRepository()
        self.llm_service = RequirementLlmService()
        self.embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.index_root = Path("scenario_rag_indexes")
        self._documents_by_project: dict[str, list[ScenarioRegistryDocument]] = {}
        self._vector_by_project: dict[str, FAISS] = {}

    def _active_project_path(self) -> str:
        value = (
            getattr(settings, "PYTHON_PROJECT_PATH", None)
            or getattr(settings, "JAVA_PROJECT_PATH", None)
            or ""
        )
        value = str(value).strip()
        if not value:
            return "ACTIVE_PROJECT"
        try:
            return str(Path(value).expanduser().resolve())
        except Exception:
            return value

    def rebuild_index(self, db: Session) -> dict:
        project_path = self._active_project_path()
        documents = self._build_documents(db)
        self._documents_by_project[project_path] = documents
        self._persist_documents(project_path, documents)
        self._build_vector_index(project_path, documents)
        counts = Counter(str(d.metadata.get("document_type") or "unknown") for d in documents)
        return {
            "status": "INDEXED",
            "project_path": project_path,
            "documents": len(documents),
            "document_types": dict(counts),
            "index_type": "HYBRID_EXACT_BM25_FAISS",
        }

    def search(self, db: Session, query: str, top_k: int = 10) -> dict:
        query = str(query or "").strip()
        if not query:
            return {"query": query, "results": [], "message": "Enter a search query."}

        project_path = self._active_project_path()
        documents = self._documents_by_project.get(project_path)
        if documents is None:
            documents = self._load_documents(project_path)
        if documents is None:
            self.rebuild_index(db)
            documents = self._documents_by_project.get(project_path, [])

        if not documents:
            return {"query": query, "results": [], "total_matches": 0}

        understanding = self._understand_query(query)
        expanded_query = self._expanded_query(query, understanding)
        query_tokens = self._meaningful_tokens(expanded_query)

        exact_scores = {}
        exact_reasons = {}
        for doc in documents:
            score, reasons = self._exact_score(doc, expanded_query, query_tokens)
            exact_scores[doc.document_id] = score
            exact_reasons[doc.document_id] = reasons

        bm25_scores = self._bm25_scores(documents, query_tokens)
        vector_scores = self._vector_scores(project_path, documents, expanded_query)

        max_exact = max(exact_scores.values(), default=0.0)
        max_bm25 = max(bm25_scores.values(), default=0.0)

        ranked = []
        for doc in documents:
            exact_raw = exact_scores.get(doc.document_id, 0.0)
            bm25_raw = bm25_scores.get(doc.document_id, 0.0)
            vector = vector_scores.get(doc.document_id, 0.0)

            exact = (exact_raw / max_exact) if max_exact > 0 else 0.0
            bm25 = (bm25_raw / max_bm25) if max_bm25 > 0 else 0.0

            # Exact/symbol evidence remains strongest; BM25 and semantic search
            # recover business-language variants.
            hybrid = (0.45 * exact) + (0.30 * bm25) + (0.25 * vector)

            doc_type = str(doc.metadata.get("document_type") or "")
            if doc_type == "test_baseline" and (exact > 0 or bm25 > 0):
                hybrid += 0.06

            ranked.append((hybrid, exact, bm25, vector, doc))

        ranked.sort(key=lambda x: x[0], reverse=True)
        best = ranked[0][0] if ranked else 0.0

        # Do not return every weak lexical match. Keep results reasonably close
        # to the best candidate and require a minimum hybrid confidence.
        threshold = max(0.30, best * 0.58)
        selected = [item for item in ranked if item[0] >= threshold]

        # If the user names an exact test-baseline identifier (for example
        # EMPLOYEE_UPDATE), keep the evidence scoped to that child baseline.
        # This prevents the parent UPDATE_DATA document from returning JIRAs
        # belonging to sibling test baselines.
        selected = self._prefer_exact_test_baseline(
            selected,
            query=query,
        )

        # Historical/release questions need evidence for the business subject,
        # not merely a broad entity match such as "employee" or "student".
        selected = self._filter_historical_evidence(
            selected,
            query=query,
            understanding=understanding,
        )

        # For release-wide questions, prefer the aggregate release document.
        # It already contains all test baselines/JIRAs and avoids repeating the
        # same answer again as individual child test-baseline cards.
        selected = self._prefer_release_aggregate(
            selected,
            query=query,
            understanding=understanding,
        )

        selected = selected[: max(1, min(int(top_k or 10), 10))]

        results = []
        for hybrid, exact, bm25, vector, doc in selected:
            reasons = list(exact_reasons.get(doc.document_id, []))
            if bm25 > 0.05:
                reasons.append("BM25 keyword relevance")
            if vector > 0.20:
                reasons.append("Semantic embedding relevance")
            results.append({
                "score": round(hybrid * 100, 1),
                "hybrid_score": round(hybrid, 4),
                "exact_score": round(exact, 4),
                "bm25_score": round(bm25, 4),
                "vector_score": round(vector, 4),
                "reasons": reasons[:5],
                "document_id": doc.document_id,
                "document_type": doc.metadata.get("document_type"),
                "scenario_id": doc.scenario_id,
                "scenario_code": doc.scenario_code,
                "title": doc.title,
                "metadata": doc.metadata,
                "summary": self._snippet(doc.text, query_tokens),
            })

        historical_answer = self._build_historical_answer(
            query=query,
            understanding=understanding,
            results=results,
        )

        return {
            "query": query,
            "expanded_query": expanded_query,
            "llm_understanding": understanding,
            "project_path": project_path,
            "total_matches": len(results),
            "candidate_documents": len(documents),
            "retrieval": "HYBRID_EXACT_BM25_FAISS",
            "historical_answer": historical_answer,
            "results": results,
        }


    def _build_historical_answer(self, query: str, understanding: dict, results: list[dict]) -> dict:
        """Create a compact, evidence-only historical answer.

        This does not ask the LLM to invent history. The LLM may help understand
        the query, but the answer below is assembled only from retrieved,
        evidence-validated Scenario Registry metadata.
        """
        raw = set(self._token_list(query))
        historical_words = {
            "history", "historical", "when", "release", "version", "jira",
            "jiras", "tested", "testing", "baseline", "covered", "executed",
            "first", "latest", "last",
        }
        is_historical = bool(raw & historical_words)
        if not is_historical:
            return {
                "is_historical_query": False,
                "status": "NOT_HISTORICAL",
                "message": None,
                "timeline": [],
            }

        if not results:
            return {
                "is_historical_query": True,
                "status": "NO_EVIDENCE",
                "message": "No captured historical evidence supports this question.",
                "timeline": [],
            }

        rows = []
        seen = set()
        for item in results:
            meta = item.get("metadata") or {}
            release_name = meta.get("release_name")
            release_version = meta.get("release_version")
            code_version = meta.get("code_baseline_version")
            tests = meta.get("test_baselines") or []
            jiras = meta.get("jira_ids") or []

            if tests:
                for test in tests:
                    row = {
                        "scenario_code": meta.get("scenario_code") or item.get("scenario_code"),
                        "http_method": meta.get("http_method"),
                        "endpoint": meta.get("endpoint"),
                        "release_name": release_name,
                        "release_version": release_version,
                        "code_baseline_version": code_version,
                        "test_baseline": test.get("name"),
                        "test_status": test.get("status"),
                        "jira_ids": test.get("jira_ids") or jiras,
                        "created_at": test.get("created_at"),
                    }
                    key = json.dumps(row, sort_keys=True, default=str)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
            else:
                row = {
                    "scenario_code": meta.get("scenario_code") or item.get("scenario_code"),
                    "http_method": meta.get("http_method"),
                    "endpoint": meta.get("endpoint"),
                    "release_name": release_name,
                    "release_version": release_version,
                    "code_baseline_version": code_version,
                    "test_baseline": meta.get("relevant_test_baseline"),
                    "test_status": meta.get("test_status"),
                    "jira_ids": jiras,
                    "created_at": None,
                }
                key = json.dumps(row, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)

        def version_number(value):
            try:
                return int(value or 0)
            except Exception:
                return 0

        rows.sort(
            key=lambda row: (
                str(row.get("release_name") or ""),
                version_number(row.get("release_version")),
                str(row.get("created_at") or ""),
            )
        )

        # Natural-language intent changes presentation, not the underlying evidence.
        if "first" in raw and rows:
            rows = [rows[0]]
        elif raw & {"latest", "last"} and rows:
            rows = [rows[-1]]

        release_labels = []
        jira_ids = []
        test_names = []
        for row in rows:
            if row.get("release_name"):
                label = str(row["release_name"])
                if row.get("release_version"):
                    label += f" V{row['release_version']}"
                if label not in release_labels:
                    release_labels.append(label)
            for jira_id in row.get("jira_ids") or []:
                if jira_id not in jira_ids:
                    jira_ids.append(jira_id)
            if row.get("test_baseline") and row["test_baseline"] not in test_names:
                test_names.append(row["test_baseline"])

        if raw & {"jira", "jiras"}:
            message = (
                "Captured JIRA evidence: " + ", ".join(jira_ids)
                if jira_ids else
                "Matching history was found, but no JIRA is captured for it."
            )
        elif raw & {"release", "version", "when", "first", "latest", "last"}:
            message = (
                "Captured release history: " + ", ".join(release_labels)
                if release_labels else
                "Matching evidence was found, but no release/version is captured for it."
            )
        elif raw & {"tested", "testing", "baseline", "executed", "covered"}:
            message = (
                "Captured testing history: " + ", ".join(test_names)
                if test_names else
                "Matching history was found, but no testing baseline is captured for it."
            )
        else:
            message = f"{len(rows)} captured historical trace(s) found."

        return {
            "is_historical_query": True,
            "status": "FOUND",
            "message": message,
            "releases": release_labels,
            "jira_ids": jira_ids,
            "test_baselines": test_names,
            "timeline": rows,
        }


    def _filter_historical_evidence(self, ranked_items, query: str, understanding: dict):
        """Ground historical answers in both domain and business-subject evidence.

        Retrieval is intentionally broad; this step is stricter.  A candidate
        must respect an explicit domain (employee/student/customer) and must
        contain evidence for the requested business subject.  Generic words
        such as eligibility/classification/validation are not enough by
        themselves.
        """
        if not ranked_items:
            return ranked_items

        raw_tokens = set(self._token_list(query))
        intent_tokens = self._meaningful_tokens(
            " ".join(str(v) for v in (understanding.get("intents") or []))
        )

        historical_words = {
            "release", "history", "historical", "tested", "testing",
            "baseline", "version", "executed", "covered", "jira", "jiras",
        }
        is_historical = bool(raw_tokens & historical_words) or bool(
            set(intent_tokens) & historical_words
        )

        # We also validate "where was X changed?" style history questions.
        change_history = bool(raw_tokens & {"change", "changed", "where"})
        if not (is_historical or change_history):
            return ranked_items

        # Explicit business domain is a hard constraint.  This prevents
        # "employee promotion" from returning a Student promotion baseline.
        domain_words = {"employee", "student", "customer"}
        requested_domains = raw_tokens & domain_words

        # These words describe the question shape rather than the business
        # subject.  They must never be the only evidence that makes a result pass.
        generic_subject_words = {
            "employee", "student", "customer", "person", "persons",
            "release", "history", "historical", "tested", "testing",
            "baseline", "version", "executed", "covered", "jira", "jiras",
            "scenario", "change", "changed", "where", "which", "what",
            "eligibility", "eligible", "classification", "validation",
            "calculation", "amount",
        }

        # Use the user's literal wording as the grounding contract. LLM-expanded
        # concepts are excellent for retrieval, but they must not manufacture
        # evidence during verification.
        subject_tokens = self._meaningful_tokens(query) - generic_subject_words

        # Release-only questions (e.g. "Which JIRAs are covered by September
        # 2026 V1?") intentionally have no business subject.
        release_only = not subject_tokens

        supported = []
        for item in ranked_items:
            doc = item[4]
            searchable_tokens = set(
                self._token_list(
                    doc.text + " " + json.dumps(doc.metadata, default=str)
                )
            )

            if requested_domains and not (requested_domains & searchable_tokens):
                continue

            if not release_only:
                lexical_subject_hits = subject_tokens & searchable_tokens

                # A historical answer needs at least one concrete business term
                # from the user's request.  This rejects scholarship/incentive/
                # bonus false positives that matched only student/employee or a
                # generic qualifier such as eligibility.
                if not lexical_subject_hits:
                    continue

            # A question asking which release tested something cannot be
            # answered by an operation that has no captured release/test evidence.
            if is_historical and raw_tokens & {"release", "tested", "testing"}:
                meta = doc.metadata or {}
                has_release = bool(meta.get("release_name")) and (
                    str(meta.get("release_name")).strip().lower() not in
                    {"unknown release", "none", ""}
                )
                has_tests = bool(meta.get("test_baselines"))
                if not (has_release and has_tests):
                    continue

            supported.append(item)

        return supported

    def _prefer_release_aggregate(self, ranked_items, query: str, understanding: dict):
        if not ranked_items:
            return ranked_items

        raw = set(self._token_list(query))
        release_scope_words = {
            "release", "version", "executed", "covered", "jiras", "jira",
        }
        asks_release_scope = bool(raw & release_scope_words)
        if not asks_release_scope:
            return ranked_items

        release_items = [
            item for item in ranked_items
            if str(item[4].metadata.get("document_type") or "") == "release"
        ]
        if not release_items:
            return ranked_items

        # If a release aggregate is present, it is the concise answer for
        # release-wide coverage/execution questions.
        release_items.sort(key=lambda x: x[0], reverse=True)
        return release_items


    def _understand_query(self, query: str) -> dict:
        if not self.llm_service.is_configured():
            return {
                "enabled": False, "used": False,
                "reason": "LLM is not configured; hybrid retrieval still used.",
                "attributes": [], "concepts": [], "intents": [],
            }
        try:
            parsed = self.llm_service.parse(query)
            return {
                "enabled": True,
                "used": True,
                "attribute": parsed.get("attribute"),
                "attributes": parsed.get("attributes") or [],
                "entity": parsed.get("entity"),
                "parent": parsed.get("parent"),
                "condition": parsed.get("condition"),
                "desired_behavior": parsed.get("desired_behavior"),
                "concepts": parsed.get("concepts") or [],
                "intents": parsed.get("intents") or [],
            }
        except Exception as exc:
            return {
                "enabled": True, "used": False,
                "reason": f"LLM understanding failed; hybrid retrieval used. {str(exc)[:250]}",
                "attributes": [], "concepts": [], "intents": [],
            }

    def _expanded_query(self, query: str, understanding: dict) -> str:
        parts = [query]
        for key in ["attribute", "entity", "parent", "condition", "desired_behavior"]:
            value = understanding.get(key)
            if value:
                parts.append(str(value))
        for key in ["attributes", "concepts", "intents"]:
            parts.extend(str(v) for v in (understanding.get(key) or []) if v)
        seen, compact = set(), []
        for value in parts:
            value = " ".join(str(value).strip().split())
            if value and value.casefold() not in seen:
                seen.add(value.casefold())
                compact.append(value)
        return " ".join(compact)

    def _build_documents(self, db: Session) -> list[ScenarioRegistryDocument]:
        scenarios = ScenarioService().get_all_for_active_project(db)
        jira_map = self._jira_map(db)
        documents: list[ScenarioRegistryDocument] = []

        for scenario in scenarios:
            code_baselines = self._find_code_baselines(db, scenario.id)
            test_baselines = self.baseline_repository.find_test_baselines(db, scenario.id)
            code_by_id = {b.id: b for b in code_baselines}

            # Operation/scenario discovery document.
            documents.append(self._scenario_only_document(scenario))

            for baseline in code_baselines:
                related_tests = [
                    t for t in test_baselines
                    if t.baseline_id == baseline.id
                    or t.code_baseline_version == baseline.baseline_version
                ]

                # Compact release document: useful for release/history questions.
                documents.append(self._release_document(scenario, baseline, related_tests))

                # Fine-grained test documents: critical for precise RAG retrieval.
                for test in related_tests:
                    documents.append(
                        self._test_baseline_document(scenario, baseline, test, jira_map)
                    )

            orphan_tests = [
                t for t in test_baselines
                if t.baseline_id and t.baseline_id not in code_by_id
            ]
            for test in orphan_tests:
                documents.append(self._test_baseline_document(scenario, None, test, jira_map))

        return documents

    def _jira_map(self, db: Session) -> dict[str, JiraKnowledge]:
        rows = db.query(JiraKnowledge).all()
        return {str(r.jira_id or "").strip().upper(): r for r in rows}

    def _find_code_baselines(self, db: Session, scenario_id: int):
        if hasattr(self.baseline_repository, "find_history"):
            return self.baseline_repository.find_history(db, scenario_id)
        return self.baseline_repository.find_all_for_scenario(db, scenario_id)

    def _scenario_only_document(self, scenario) -> ScenarioRegistryDocument:
        metadata = {
            **self._scenario_metadata(scenario),
            "document_type": "scenario",
            "jira_ids": self._normalize_list(scenario.jira_id),
            "test_baselines": [],
        }
        text = self._join([
            f"Operation scenario {scenario.scenario_code}",
            scenario.scenario_name,
            scenario.description,
            f"Endpoint {scenario.http_method} {scenario.endpoint}",
        ])
        return ScenarioRegistryDocument(
            f"scenario:{scenario.id}", scenario.id, scenario.scenario_code,
            f"{scenario.scenario_code} · Operation", text, metadata
        )

    def _release_document(self, scenario, baseline: ScenarioBaseline, tests: list) -> ScenarioRegistryDocument:
        release_name = baseline.baseline_name or "Release"
        release_version = baseline.release_version or baseline.baseline_version
        jira_ids = sorted({
            j for test in tests for j in self._normalize_list(test.jira_ids)
        })
        metadata = {
            **self._scenario_metadata(scenario),
            "document_type": "release",
            "release_name": release_name,
            "release_version": release_version,
            "code_baseline_version": baseline.baseline_version,
            "is_active": bool(baseline.is_active),
            "jira_ids": jira_ids,
            "test_baselines": [
                {
                    "name": t.baseline_name,
                    "status": t.status,
                    "jira_ids": self._normalize_list(t.jira_ids),
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                } for t in tests
            ],
        }
        text = self._join([
            f"Release baseline for {scenario.scenario_code}",
            f"Endpoint {scenario.http_method} {scenario.endpoint}",
            f"Release {release_name} V{release_version}",
            f"Code baseline V{baseline.baseline_version}",
            "Test baselines " + ", ".join(t.baseline_name for t in tests),
            "JIRAs " + ", ".join(jira_ids),
        ])
        return ScenarioRegistryDocument(
            f"scenario:{scenario.id}:release:{baseline.id}",
            scenario.id, scenario.scenario_code,
            f"{scenario.scenario_code} · {release_name} V{release_version}", text, metadata
        )

    def _test_baseline_document(self, scenario, baseline, test, jira_map) -> ScenarioRegistryDocument:
        jira_ids = self._normalize_list(test.jira_ids)
        jira_text = []
        for jira_id in jira_ids:
            row = jira_map.get(jira_id)
            if row:
                jira_text.extend([
                    f"JIRA {jira_id}",
                    f"JIRA title {row.title}" if row.title else None,
                    f"JIRA requirement {row.requirement}" if row.requirement else None,
                ])

        release_name = (baseline.baseline_name if baseline else None) or "Unknown Release"
        release_version = (
            (baseline.release_version or baseline.baseline_version)
            if baseline else test.code_baseline_version
        )
        code_version = baseline.baseline_version if baseline else test.code_baseline_version

        metadata = {
            **self._scenario_metadata(scenario),
            "document_type": "test_baseline",
            "release_name": release_name,
            "release_version": release_version,
            "code_baseline_version": code_version,
            "jira_ids": jira_ids,
            "relevant_test_baseline": test.baseline_name,
            "test_status": test.status,
            "test_baselines": [{
                "name": test.baseline_name,
                "status": test.status,
                "jira_ids": jira_ids,
                "created_at": test.created_at.isoformat() if test.created_at else None,
            }],
        }

        text = self._join([
            f"Test baseline {test.baseline_name}",
            f"Parent scenario {scenario.scenario_code}",
            f"Endpoint {scenario.http_method} {scenario.endpoint}",
            f"Release {release_name} V{release_version}",
            f"Status {test.status}",
            *jira_text,
            f"Request {test.request_json}" if test.request_json else None,
            f"Expected response {test.expected_response_json}" if test.expected_response_json else None,
            f"Actual response {test.actual_response_json}" if test.actual_response_json else None,
            f"Expected database effect {test.expected_db_effect}" if test.expected_db_effect else None,
        ])

        return ScenarioRegistryDocument(
            f"scenario:{scenario.id}:test:{test.id}:release:{getattr(baseline, 'id', 'orphan')}",
            scenario.id, scenario.scenario_code,
            f"{test.baseline_name} · {release_name} V{release_version}", text, metadata
        )

    @staticmethod
    def _scenario_metadata(scenario) -> dict:
        return {
            "scenario_id": scenario.id,
            "scenario_code": scenario.scenario_code,
            "scenario_name": scenario.scenario_name,
            "http_method": scenario.http_method,
            "endpoint": scenario.endpoint,
            "scenario_jira_id": scenario.jira_id,
        }

    def _prefer_exact_test_baseline(self, selected, query):
        """Prefer an individual test-baseline document when its identifier is
        explicitly present in the user's query.

        The aggregate scenario/release documents intentionally contain all
        child JIRAs, so they are useful for broad questions but are too broad
        for an exact baseline question such as EMPLOYEE_UPDATE.
        """
        if not selected:
            return selected

        q_compact = re.sub(r"[^a-z0-9]", "", str(query or "").lower())
        exact_children = []

        for item in selected:
            doc = item[4]
            if str(doc.metadata.get("document_type") or "") != "test_baseline":
                continue

            baseline = str(doc.metadata.get("relevant_test_baseline") or "").strip()
            baseline_compact = re.sub(r"[^a-z0-9]", "", baseline.lower())
            if baseline_compact and baseline_compact in q_compact:
                exact_children.append(item)

        # Exact named baseline is a deterministic scope constraint, not merely
        # another ranking hint. Returning sibling/parent evidence here can
        # attach unrelated JIRAs to the answer.
        return exact_children if exact_children else selected

    def _exact_score(self, document, query, query_tokens):
        searchable = self._search_text(document.text + "\n" + json.dumps(document.metadata, default=str))
        query_norm = self._search_text(query)
        score, reasons = 0.0, []

        if query_norm and query_norm in searchable:
            score += 80
            reasons.append("Exact phrase matched")

        for token in query_tokens:
            occurrences = searchable.count(token)
            if occurrences:
                score += min(occurrences * 12, 48)
                reasons.append(f"Matched `{token}`")

        # Strong exact identifiers.
        identifiers = [
            document.metadata.get("scenario_code"),
            document.metadata.get("relevant_test_baseline"),
            *(document.metadata.get("jira_ids") or []),
        ]
        q_compact = re.sub(r"[^a-z0-9]", "", query.lower())
        for value in identifiers:
            compact = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
            if compact and compact in q_compact:
                score += 90
                reasons.append(f"Matched identifier `{value}`")

        return score, reasons[:5]

    def _bm25_scores(self, documents, query_tokens):
        tokens = list(query_tokens)
        if not tokens:
            return {d.document_id: 0.0 for d in documents}

        corpus = [
            self._token_list(
                d.text + " " + json.dumps(d.metadata, default=str)
            )
            for d in documents
        ]

        # rank-bm25 handles term frequency, IDF, and document-length normalization.
        bm25 = BM25Okapi(
            corpus,
            k1=1.5,
            b=0.75,
        )

        raw_scores = bm25.get_scores(tokens)

        return {
            document.document_id: float(score)
            for document, score in zip(documents, raw_scores)
        }

    def _build_vector_index(self, project_path, documents):
        if not documents:
            self._vector_by_project.pop(project_path, None)
            return
        lc_docs = [
            Document(page_content=d.text, metadata={"document_id": d.document_id})
            for d in documents
        ]
        store = FAISS.from_documents(lc_docs, self.embeddings)
        vector_path = self._project_index_path(project_path) / "faiss"
        vector_path.mkdir(parents=True, exist_ok=True)
        store.save_local(str(vector_path))
        self._vector_by_project[project_path] = store

    def _vector_scores(self, project_path, documents, query):
        store = self._vector_by_project.get(project_path)
        if store is None:
            store = self._load_vector_index(project_path)
        if store is None:
            self._build_vector_index(project_path, documents)
            store = self._vector_by_project.get(project_path)
        if store is None:
            return {}

        try:
            matches = store.similarity_search_with_score(query, k=len(documents))
        except Exception:
            return {}

        scores = {}
        for doc, distance in matches:
            # FAISS returns distance: smaller is better. Convert to bounded relevance.
            relevance = 1.0 / (1.0 + max(float(distance), 0.0))
            scores[str(doc.metadata.get("document_id"))] = relevance
        return scores

    def _load_vector_index(self, project_path):
        vector_path = self._project_index_path(project_path) / "faiss"
        if not (vector_path / "index.faiss").exists():
            return None
        try:
            store = FAISS.load_local(
                str(vector_path), self.embeddings,
                allow_dangerous_deserialization=True,
            )
            self._vector_by_project[project_path] = store
            return store
        except Exception:
            return None

    def _meaningful_tokens(self, value):
        return {
            t for t in self._token_list(value)
            if len(t) >= 2 and t not in self.STOP_WORDS
        }

    def _token_list(self, value):
        tokens = []
        text = str(value or "")
        for raw in re.split(r"[^A-Za-z0-9_]+", text):
            if not raw:
                continue
            variants = self._name_variants(raw)
            tokens.extend(v for v in variants if " " not in v and "_" not in v and len(v) >= 2)
        return tokens

    @staticmethod
    def _search_text(value):
        text = str(value or "")
        variants = [text.lower()]
        for raw in re.split(r"[^A-Za-z0-9_]+", text):
            variants.extend(ScenarioRagRegistryService._name_variants(raw))
        return " ".join(v for v in variants if v)

    @staticmethod
    def _name_variants(value):
        value = str(value or "").strip()
        if not value:
            return set()
        spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
        spaced = spaced.replace("_", " ").replace("-", " ")
        words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w.strip()]
        variants = {value.lower(), spaced.lower()}
        variants.update(words)
        if words:
            variants.update({" ".join(words), "_".join(words), "".join(words)})
        return variants

    def _snippet(self, text, query_tokens):
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        lower = normalized.lower()
        positions = [lower.find(t) for t in query_tokens if t in lower]
        first = min(positions) if positions else 0
        start = max(0, first - 70)
        end = min(len(normalized), first + 230)
        return ("..." if start else "") + normalized[start:end] + ("..." if end < len(normalized) else "")

    @staticmethod
    def _normalize_list(value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip().upper() for x in value if str(x or "").strip()]
        return [str(value).strip().upper()] if str(value or "").strip() else []

    @staticmethod
    def _join(values):
        parts = []
        for value in values:
            if value is None:
                continue
            if isinstance(value, str):
                if value.strip():
                    parts.append(value.strip())
            else:
                try:
                    parts.append(json.dumps(value, default=str, ensure_ascii=False))
                except Exception:
                    parts.append(str(value))
        return "\n".join(parts)

    def _persist_documents(self, project_path, documents):
        index_path = self._project_index_path(project_path)
        index_path.mkdir(parents=True, exist_ok=True)
        payload = [{
            "document_id": d.document_id,
            "scenario_id": d.scenario_id,
            "scenario_code": d.scenario_code,
            "title": d.title,
            "text": d.text,
            "metadata": d.metadata,
        } for d in documents]
        (index_path / "scenario_registry.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    def _load_documents(self, project_path):
        file_path = self._project_index_path(project_path) / "scenario_registry.json"
        if not file_path.exists():
            return None
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            documents = [
                ScenarioRegistryDocument(
                    document_id=i["document_id"],
                    scenario_id=int(i["scenario_id"]),
                    scenario_code=i["scenario_code"],
                    title=i["title"],
                    text=i["text"],
                    metadata=i.get("metadata") or {},
                ) for i in payload
            ]
            self._documents_by_project[project_path] = documents
            return documents
        except Exception:
            return None

    def _project_index_path(self, project_path):
        digest = hashlib.sha1(project_path.encode("utf-8", errors="ignore")).hexdigest()[:12]
        name = Path(project_path).name or "project"
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return self.index_root / f"{safe_name}-{digest}"


scenario_rag_registry_service = ScenarioRagRegistryService()
