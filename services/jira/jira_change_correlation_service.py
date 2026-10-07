from __future__ import annotations

import json
import re
from typing import Any

from db_models import JiraKnowledge


class JiraChangeCorrelationService:
    """Correlate the current code change and its test evidence with JIRA knowledge.

    This is intentionally an evidence correlator, not an authority generator.
    A high-scoring issue is returned as LIKELY_CURRENT_JIRA until a current-change
    relationship is explicitly confirmed elsewhere. Historical scenario/baseline
    JIRAs are supplied by the caller and are kept out of the current-candidate set.
    """

    _STOP_WORDS = {
        "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "is",
        "are", "be", "with", "from", "this", "that", "when", "then", "change",
        "changed", "update", "updated", "current", "new", "old", "api", "data",
    }

    @staticmethod
    def _tokens(value: Any) -> set[str]:
        text = str(value or "").lower()
        return {
            token for token in re.findall(r"[a-z][a-z0-9_]{1,}", text)
            if token not in JiraChangeCorrelationService._STOP_WORDS
        }

    @staticmethod
    def _numbers(value: Any) -> set[str]:
        return set(re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", str(value or "")))

    @staticmethod
    def _operator_threshold(change: dict[str, Any], prefix: str) -> tuple[str | None, str | None]:
        operator = change.get(f"{prefix}_operator")
        threshold = change.get(f"{prefix}_threshold")
        return (
            str(operator).strip() if operator is not None else None,
            str(threshold).strip() if threshold is not None else None,
        )

    @staticmethod
    def _contains_rule(text: str, operator: str | None, threshold: str | None) -> bool:
        if not threshold:
            return False
        escaped = re.escape(threshold)
        if operator:
            # Accept compact forms (>6.5) and natural requirement wording (> 6.5).
            return re.search(rf"{re.escape(operator)}\s*{escaped}(?![\d.])", text) is not None
        return re.search(rf"(?<![\d.]){escaped}(?![\d.])", text) is not None

    @staticmethod
    def _json_text(value: Any) -> str:
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except Exception:
            return str(value or "")

    def _evidence_model(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = result.get("change_summary") or {}
        code_terms: set[str] = set()
        test_terms: set[str] = set()
        scenario_terms: set[str] = set()

        attributes = [str(v) for v in (summary.get("changed_attributes") or []) if v]
        classes = [str(v) for v in (summary.get("changed_classes") or []) if v]
        methods = [
            str(m.get("method_name") or m.get("method") or "")
            for m in (summary.get("changed_methods") or []) if isinstance(m, dict)
        ]
        for value in attributes + classes + methods:
            code_terms |= self._tokens(value)

        behavior = []
        for change in result.get("behavioral_changes") or []:
            if not isinstance(change, dict):
                continue
            old_op, old_threshold = self._operator_threshold(change, "old")
            new_op, new_threshold = self._operator_threshold(change, "new")
            behavior.append({
                "attribute": change.get("attribute"),
                "old_operator": old_op,
                "old_threshold": old_threshold,
                "new_operator": new_op,
                "new_threshold": new_threshold,
                "old_condition": change.get("old_condition"),
                "new_condition": change.get("new_condition"),
            })
            code_terms |= self._tokens(change.get("attribute"))
            code_terms |= self._tokens(change.get("new_condition"))

        scenarios = []
        for scenario in result.get("affected_scenarios") or []:
            if not isinstance(scenario, dict):
                continue
            code = str(scenario.get("scenario_code") or "")
            endpoint = str(scenario.get("endpoint") or "")
            method = str(scenario.get("http_method") or "")
            scenarios.append({"scenario_code": code, "endpoint": endpoint, "http_method": method})
            scenario_terms |= self._tokens(code)
            scenario_terms |= self._tokens(endpoint)

        captured = result.get("captured_test_baseline_evidence") or {}
        baseline_names: list[str] = []
        for scenario_code, baselines in captured.items():
            scenario_terms |= self._tokens(scenario_code)
            for baseline in baselines or []:
                if not isinstance(baseline, dict):
                    continue
                name = str(baseline.get("test_baseline_name") or baseline.get("baseline_name") or "")
                if name:
                    baseline_names.append(name)
                    test_terms |= self._tokens(name)
                test_terms |= self._tokens(self._json_text(baseline))

        automated_tests: list[str] = []
        for rec in result.get("regression_recommendations") or []:
            for test in (rec.get("automated_test_evidence") or []):
                if not isinstance(test, dict):
                    continue
                label = ".".join(filter(None, [str(test.get("test_class") or ""), str(test.get("test_method") or "")]))
                if label:
                    automated_tests.append(label)
                    test_terms |= self._tokens(label)

        for boundary in result.get("boundary_regression_recommendations") or []:
            test_terms |= self._tokens(self._json_text(boundary))

        return {
            "attributes": attributes,
            "classes": classes,
            "methods": [m for m in methods if m],
            "behavioral_changes": behavior,
            "scenarios": scenarios,
            "baseline_names": baseline_names,
            "automated_tests": automated_tests,
            "code_terms": code_terms,
            "scenario_terms": scenario_terms,
            "test_terms": test_terms,
        }

    def correlate(
        self,
        db,
        result: dict[str, Any],
        historical_jira_ids: set[str] | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        historical = {str(v).upper() for v in (historical_jira_ids or set()) if v}
        evidence = self._evidence_model(result)
        project_path = str(result.get("project_path") or "").strip().lower()

        query = db.query(JiraKnowledge)
        rows = query.order_by(JiraKnowledge.updated_at.desc(), JiraKnowledge.id.desc()).all()
        candidates: list[dict[str, Any]] = []

        for row in rows:
            jira_id = str(row.jira_id or "").upper()
            row_project = str(row.project_path or "").strip().lower()
            if project_path and row_project and row_project != project_path:
                continue
            if jira_id in historical:
                continue

            jira_text = "\n".join(filter(None, [row.title or "", row.requirement or ""]))
            lower_text = jira_text.lower()
            jira_tokens = self._tokens(jira_text)
            score = 0
            reasons: list[dict[str, Any]] = []

            matched_attributes = sorted({a for a in evidence["attributes"] if self._tokens(a) & jira_tokens})
            if matched_attributes:
                points = min(20, 12 + 4 * len(matched_attributes))
                score += points
                reasons.append({"type": "CODE_ATTRIBUTE_MATCH", "points": points, "matches": matched_attributes})

            matched_code = sorted((evidence["code_terms"] & jira_tokens) - set(matched_attributes))
            if matched_code:
                points = min(12, len(matched_code) * 2)
                score += points
                reasons.append({"type": "CODE_SEMANTIC_MATCH", "points": points, "matches": matched_code[:10]})

            matched_scenario = sorted(evidence["scenario_terms"] & jira_tokens)
            if matched_scenario:
                points = min(15, 5 + len(matched_scenario) * 2)
                score += points
                reasons.append({"type": "SCENARIO_OPERATION_MATCH", "points": points, "matches": matched_scenario[:10]})

            matched_test = sorted(evidence["test_terms"] & jira_tokens)
            if matched_test:
                points = min(15, 5 + len(matched_test) * 2)
                score += points
                reasons.append({"type": "TEST_EVIDENCE_MATCH", "points": points, "matches": matched_test[:10]})

            new_rule_match = False
            old_rule_only = False
            for change in evidence["behavioral_changes"]:
                new_match = self._contains_rule(lower_text, change.get("new_operator"), change.get("new_threshold"))
                old_match = self._contains_rule(lower_text, change.get("old_operator"), change.get("old_threshold"))
                if new_match:
                    new_rule_match = True
                    score += 38
                    reasons.append({
                        "type": "NEW_BEHAVIOR_RULE_MATCH",
                        "points": 38,
                        "attribute": change.get("attribute"),
                        "operator": change.get("new_operator"),
                        "threshold": change.get("new_threshold"),
                    })
                if old_match and not new_match and change.get("old_threshold") != change.get("new_threshold"):
                    old_rule_only = True

            if old_rule_only and not new_rule_match:
                score -= 25
                reasons.append({
                    "type": "OLD_BEHAVIOR_ONLY_PENALTY",
                    "points": -25,
                    "note": "JIRA text matches the previous behavior but not the new behavior.",
                })

            # Small lexical overlap bonus helps requirements that describe the same
            # intent without repeating exact class/scenario identifiers.
            all_evidence_terms = evidence["code_terms"] | evidence["scenario_terms"] | evidence["test_terms"]
            overlap = sorted(all_evidence_terms & jira_tokens)
            if overlap:
                bonus = min(10, len(overlap))
                score += bonus
                reasons.append({"type": "CROSS_EVIDENCE_OVERLAP", "points": bonus, "matches": overlap[:12]})

            score = max(0, min(100, score))
            if score < 25:
                continue
            confidence = "HIGH" if score >= 70 else "MEDIUM" if score >= 45 else "LOW"
            candidates.append({
                "jira_id": jira_id,
                "title": row.title,
                "requirement": row.requirement,
                "classification": "LIKELY_CURRENT_JIRA",
                "confidence": confidence,
                "confidence_score": score,
                "authoritative": False,
                "candidate_source": "CODE_AND_TEST_CORRELATION",
                "evidence_sources": ["CURRENT_CODE_CHANGE", "AFFECTED_SCENARIOS", "TEST_EVIDENCE", "JIRA_KNOWLEDGE"],
                "why_matched": reasons,
                "confirmation_required": True,
                "note": "Candidate only. Code/test correlation does not create an authoritative current-change Jira relationship.",
            })

        candidates.sort(key=lambda item: (-item["confidence_score"], item["jira_id"]))
        candidates = candidates[: max(1, limit)]
        return {
            "candidates": candidates,
            "candidate_count": len(candidates),
            "search_direction": "CURRENT_CODE_AND_TEST_EVIDENCE_TO_JIRA",
            "evidence_model": {
                "attributes": evidence["attributes"],
                "classes": evidence["classes"],
                "methods": evidence["methods"],
                "behavioral_changes": evidence["behavioral_changes"],
                "scenarios": evidence["scenarios"],
                "baseline_names": evidence["baseline_names"],
                "automated_tests": evidence["automated_tests"],
            },
            "policy": "Candidates are discovery evidence until explicitly confirmed or linked by current test-baseline/current-change evidence.",
        }


jira_change_correlation_service = JiraChangeCorrelationService()
