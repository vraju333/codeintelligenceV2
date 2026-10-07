from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from config import settings
from services.flow.deep_code_intelligence_service import DeepCodeIntelligenceService
from services.lineage.mapping_intelligence_service import MappingIntelligenceService
from services.regression.regression_impact_service import RegressionImpactService
from services.release_intelligence.release_intelligence_service import ReleaseIntelligenceService
from services.retrieval.enterprise_hybrid_rag_service import EnterpriseHybridRagService


class RegressionReleaseIntelligenceService:
    """Evidence-grounded regression recommendation and release readiness."""

    _COND_RE = re.compile(r"(?P<lhs>[A-Za-z_$][\w$\.()]*?)\s*(?P<op>>=|<=|>|<|==|!=)\s*(?P<num>-?\d+(?:\.\d+)?)")

    def __init__(self) -> None:
        self.regression = RegressionImpactService()
        self.release = ReleaseIntelligenceService()
        self.deep = DeepCodeIntelligenceService()
        self.mapping = MappingIntelligenceService()
        self.hybrid = EnterpriseHybridRagService()

    @staticmethod
    def _safe(callable_, fallback: Any) -> Any:
        try:
            return callable_()
        except Exception as exc:
            if isinstance(fallback, dict):
                return {**fallback, "error": str(exc)}
            return fallback

    @staticmethod
    def _uniq(values) -> list[str]:
        return sorted({str(v).strip() for v in (values or []) if str(v).strip()}, key=str.lower)

    @staticmethod
    def _number(value: str) -> float:
        return float(value)

    @staticmethod
    def _display_number(value: float) -> int | float:
        return int(value) if float(value).is_integer() else round(value, 10)

    def _current_git_diff(self) -> str:
        root = Path(settings.JAVA_PROJECT_PATH).resolve()
        proc = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--unified=0", "--", "*.java"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout or ""

    def _behavioral_changes(self, attrs: list[str]) -> list[dict[str, Any]]:
        """Compare removed/added Java conditions in the current Git diff.

        Only evidence visible in the Git diff is emitted. Numeric threshold changes
        receive deterministic boundary-value recommendations.
        """
        diff = self._safe(self._current_git_diff, "")
        if not diff:
            return []

        current_file = None
        removed: list[tuple[str | None, str]] = []
        added: list[tuple[str | None, str]] = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
            elif line.startswith("-") and not line.startswith("---"):
                removed.append((current_file, line[1:].strip()))
            elif line.startswith("+") and not line.startswith("+++"):
                added.append((current_file, line[1:].strip()))

        attr_lower = {a.lower() for a in attrs}
        changes: list[dict[str, Any]] = []
        used_new: set[int] = set()
        for old_file, old_line in removed:
            old_matches = list(self._COND_RE.finditer(old_line))
            if not old_matches:
                continue
            for old_match in old_matches:
                old_lhs = old_match.group("lhs")
                if attr_lower and not any(a in old_lhs.lower() or a in old_line.lower() for a in attr_lower):
                    continue
                for idx, (new_file, new_line) in enumerate(added):
                    if idx in used_new or new_file != old_file:
                        continue
                    new_matches = list(self._COND_RE.finditer(new_line))
                    candidate = next((m for m in new_matches if m.group("lhs").lower() == old_lhs.lower()), None)
                    if candidate is None:
                        continue
                    old_op, new_op = old_match.group("op"), candidate.group("op")
                    old_num, new_num = self._number(old_match.group("num")), self._number(candidate.group("num"))
                    if old_op == new_op and old_num == new_num:
                        continue
                    used_new.add(idx)
                    attr = next((a for a in attrs if a.lower() in old_lhs.lower() or a.lower() in old_line.lower()), None)
                    boundary_tests = self._boundary_tests(attr or old_lhs, new_op, new_num)
                    changes.append({
                        "type": "NUMERIC_CONDITION_CHANGE",
                        "attribute": attr,
                        "file": old_file,
                        "old_condition": old_line,
                        "new_condition": new_line,
                        "old_operator": old_op,
                        "new_operator": new_op,
                        "old_threshold": self._display_number(old_num),
                        "new_threshold": self._display_number(new_num),
                        "boundary_regression_tests": boundary_tests,
                        "evidence": "CURRENT_GIT_DIFF",
                    })
                    break
        return changes

    def _boundary_tests(self, attribute: str, operator: str, threshold: float) -> list[dict[str, Any]]:
        delta = 0.01 if abs(threshold) < 1000 else max(abs(threshold) * 0.001, 0.01)
        values = [threshold - delta, threshold, threshold + delta]

        def expected(v: float) -> bool:
            return {">": v > threshold, ">=": v >= threshold, "<": v < threshold,
                    "<=": v <= threshold, "==": v == threshold, "!=": v != threshold}[operator]

        tests = []
        for value in values:
            tests.append({
                "attribute": attribute,
                "value": self._display_number(value),
                "expected_condition_result": expected(value),
                "reason": "BOUNDARY_BELOW" if value < threshold else "BOUNDARY_AT" if value == threshold else "BOUNDARY_ABOVE",
            })
        tests.append({
            "attribute": attribute,
            "value": None,
            "expected_condition_result": None,
            "reason": "NULL_BEHAVIOR_MUST_BE_VERIFIED_WHEN_ATTRIBUTE_IS_NULLABLE",
        })
        return tests

    @staticmethod
    def _source_proves_attribute(deep: dict[str, Any], target: str) -> bool:
        target_attr = str(target or "").split(".")[-1].lower()
        if not target_attr or str(deep.get("status") or "").upper() != "FOUND":
            return False
        if str(deep.get("attribute") or "").lower() == target_attr:
            return True
        needle = target_attr.lower()
        for method in deep.get("direct_methods") or []:
            if needle in str(method.get("qualified_method") or "").lower():
                return True
        for edge in deep.get("data_lineage") or []:
            if needle in str(edge).lower():
                return True
        return False

    def _reconcile_mapping_quality(self, quality: dict[str, Any], deep: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Do not call a target missing when current source proves it exists.

        A disagreement between Neo4j and current source becomes GRAPH_SYNC_MISMATCH.
        Genuine missing targets remain TARGET_NOT_FOUND_IN_CODE.
        """
        issues_out: list[dict[str, Any]] = []
        consistency: list[dict[str, Any]] = []
        for issue in quality.get("issues") or []:
            issue_type = str(issue.get("type") or issue.get("issue_type") or "").upper()
            if "TARGET_NOT_FOUND" not in issue_type:
                issues_out.append(issue)
                continue
            genuine = []
            for definition in issue.get("definitions") or []:
                target = definition.get("target") or definition.get("target_expression")
                if self._source_proves_attribute(deep, target):
                    consistency.append({
                        "severity": "MEDIUM",
                        "type": "GRAPH_SYNC_MISMATCH",
                        "target": target,
                        "source_status": "FOUND_IN_CURRENT_SOURCE",
                        "graph_status": definition.get("status") or "NOT_FOUND_IN_CODE_GRAPH",
                        "action": "Re-sync the engineering knowledge graph before using graph absence as release evidence.",
                        "evidence": definition.get("evidence") or {},
                    })
                else:
                    genuine.append(definition)
            if genuine:
                issues_out.append({**issue, "definitions": genuine})
        result = {**quality, "issues": issues_out}
        summary = dict(result.get("summary") or {})
        summary["issues"] = len(issues_out)
        summary["high"] = sum(1 for i in issues_out if str(i.get("severity") or "").upper() == "HIGH")
        summary["medium"] = sum(1 for i in issues_out if str(i.get("severity") or "").upper() == "MEDIUM")
        summary["low"] = sum(1 for i in issues_out if str(i.get("severity") or "").upper() == "LOW")
        result["summary"] = summary
        if consistency:
            result["evidence_consistency"] = consistency
        return result, consistency

    @staticmethod
    def _requirement_mismatches(attribute: str, behavioral_changes: list[dict[str, Any]], discovery: dict[str, Any]) -> list[dict[str, Any]]:
        mismatches = []
        relevant_changes = [c for c in behavioral_changes if str(c.get("attribute") or "").lower() == attribute.lower()]
        if not relevant_changes:
            return mismatches
        patterns = [
            (re.compile(r"greater\s+than\s+(-?\d+(?:\.\d+)?)", re.I), ">"),
            (re.compile(r"less\s+than\s+(-?\d+(?:\.\d+)?)", re.I), "<"),
            (re.compile(r">=\s*(-?\d+(?:\.\d+)?)"), ">="),
            (re.compile(r"<=\s*(-?\d+(?:\.\d+)?)"), "<="),
            (re.compile(r">\s*(-?\d+(?:\.\d+)?)"), ">"),
            (re.compile(r"<\s*(-?\d+(?:\.\d+)?)"), "<"),
        ]
        for result in discovery.get("results") or []:
            if str(result.get("source_type") or "").upper() not in {"REQUIREMENT", "JIRA", "RELEASE", "ARCHITECTURE"}:
                continue
            text = f"{result.get('title') or ''}\n{result.get('snippet') or ''}"
            if attribute.lower() not in text.lower():
                continue
            for pattern, op in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                documented = float(match.group(1))
                for change in relevant_changes:
                    if op != change.get("new_operator") or documented != float(change.get("new_threshold")):
                        mismatches.append({
                            "severity": "HIGH",
                            "type": "REQUIREMENT_CODE_MISMATCH",
                            "attribute": attribute,
                            "document_source_type": result.get("source_type"),
                            "document_title": result.get("title"),
                            "documented_operator": op,
                            "documented_threshold": int(documented) if documented.is_integer() else documented,
                            "current_code_operator": change.get("new_operator"),
                            "current_code_threshold": change.get("new_threshold"),
                            "current_code_file": change.get("file"),
                            "evidence_rule": "Document text is Phase 8 discovery evidence; mismatch must be reviewed against the authoritative requirement/JIRA before release.",
                        })
                        break
                break
        # de-duplicate same document/threshold
        unique = {}
        for item in mismatches:
            key = (item.get("document_source_type"), item.get("document_title"), item.get("documented_operator"), item.get("documented_threshold"))
            unique[key] = item
        return list(unique.values())

    def status(self) -> dict[str, Any]:
        return {
            "status": "READY",
            "phase": 10,
            "capability": "REGRESSION_AND_RELEASE_INTELLIGENCE",
            "project_path": str(Path(settings.JAVA_PROJECT_PATH).resolve()),
            "evidence_sources": [
                "CURRENT_GIT_CHANGE", "SCENARIO_IMPACT", "TEST_BASELINE", "STATIC_TEST_SOURCE",
                "DEEP_CODE", "MAPPING_CONTRACT", "HYBRID_RAG_DISCOVERY",
            ],
            "release_gate": "EVIDENCE_BASED_NOT_AUTOMATIC_APPROVAL",
        }

    def analyse(self, db) -> dict[str, Any]:
        impact = self.regression.analyse(db)
        base = self.release.analyse(db)
        attrs = self._uniq(impact.get("changed_attributes"))
        affected = impact.get("affected_scenarios") or []
        behavioral_changes = self._behavioral_changes(attrs)

        attribute_intelligence = []
        consistency_findings: list[dict[str, Any]] = []
        requirement_mismatches: list[dict[str, Any]] = []
        for attr in attrs:
            deep = self._safe(lambda a=attr: self.deep.analyze(a), {"status": "UNAVAILABLE", "attribute": attr})
            mappings = self._safe(lambda a=attr: self.mapping.lineage(db, a), {"attribute": attr, "mappings": [], "mapping_count": 0, "conflicts": []})
            raw_quality = self._safe(lambda a=attr: self.mapping.quality(db, attribute=a), {"attribute": attr, "issues": [], "summary": {}})
            quality, consistency = self._reconcile_mapping_quality(raw_quality, deep)
            consistency_findings.extend({"attribute": attr, **c} for c in consistency)
            discovery = self._safe(
                lambda a=attr: self.hybrid.search(query=f"{a} change regression requirement test release mapping", top_k=5,
                                                   project_path=str(Path(settings.JAVA_PROJECT_PATH).resolve())),
                {"query": attr, "results": [], "status": "UNAVAILABLE"},
            )
            mismatches = self._requirement_mismatches(attr, behavioral_changes, discovery)
            requirement_mismatches.extend(mismatches)
            attribute_intelligence.append({
                "attribute": attr, "deep_code": deep, "mapping_lineage": mappings,
                "mapping_quality": quality, "related_knowledge": discovery,
                "requirement_code_mismatches": mismatches,
            })

        gaps = list(base.get("coverage_gaps") or [])
        mapping_issue_count = 0
        missing_mapping_targets = 0
        for item in attribute_intelligence:
            issues = (item.get("mapping_quality") or {}).get("issues") or []
            mapping_issue_count += len(issues)
            for issue in issues:
                issue_type = str(issue.get("type") or issue.get("issue_type") or "").upper()
                if "TARGET_NOT_FOUND" in issue_type:
                    missing_mapping_targets += len(issue.get("definitions") or []) or 1
                if str(issue.get("severity") or "").upper() in {"HIGH", "CRITICAL"}:
                    gaps.append({"attribute": item["attribute"], "gap_type": "MAPPING_QUALITY_RISK", "detail": issue})

        for mismatch in requirement_mismatches:
            gaps.append({"attribute": mismatch.get("attribute"), "gap_type": "REQUIREMENT_CODE_MISMATCH", "detail": mismatch})

        # Boundary coverage is a gap until captured evidence proves the new boundary.
        for change in behavioral_changes:
            gaps.append({
                "attribute": change.get("attribute"),
                "gap_type": "BEHAVIORAL_BOUNDARY_COVERAGE_REQUIRED",
                "detail": {
                    "old_condition": change.get("old_condition"),
                    "new_condition": change.get("new_condition"),
                    "recommended_boundary_tests": change.get("boundary_regression_tests"),
                },
            })

        failed = int((base.get("summary") or {}).get("failed_test_baselines") or 0)
        no_changes = int(impact.get("total_changed_java_files") or 0) == 0
        if no_changes:
            readiness = "NO_CHANGES"
        elif failed > 0:
            readiness = "BLOCKED"
        elif gaps or consistency_findings:
            readiness = "REVIEW_REQUIRED"
        else:
            readiness = "READY_FOR_RELEASE_REVIEW"

        recommended_tests = []
        for rec in base.get("regression_recommendations") or []:
            recommended_tests.append({
                "scenario_code": rec.get("scenario_code"),
                "operation": f"{rec.get('http_method') or ''} {rec.get('endpoint') or ''}".strip(),
                "impact_status": rec.get("impact_status"),
                "existing_test_baselines": rec.get("test_baselines") or [],
                "automated_test_evidence": rec.get("automated_test_evidence") or [],
                "recommended_action": rec.get("recommended_action"),
                "reason": rec.get("reasons") or [],
            })

        return {
            "status": "ANALYZED", "phase": 10,
            "project_path": str(Path(settings.JAVA_PROJECT_PATH).resolve()),
            "change_summary": {
                "changed_java_files": impact.get("total_changed_java_files", 0),
                "changed_classes": impact.get("changed_classes") or [],
                "changed_methods": impact.get("changed_methods") or [],
                "changed_attributes": attrs,
                "changed_symbols": impact.get("changed_symbols") or [],
            },
            "behavioral_changes": behavioral_changes,
            "impact_summary": {
                "affected_scenarios": len(affected), "mapping_quality_issues": mapping_issue_count,
                "missing_mapping_targets": missing_mapping_targets, "coverage_gaps": len(gaps),
                "failed_test_baselines": failed, "requirement_code_mismatches": len(requirement_mismatches),
                "graph_sync_mismatches": len(consistency_findings),
            },
            "affected_scenarios": affected,
            "regression_recommendations": recommended_tests,
            "boundary_regression_recommendations": [t for c in behavioral_changes for t in c.get("boundary_regression_tests") or []],
            "coverage_gaps": gaps,
            "requirement_code_mismatches": requirement_mismatches,
            "evidence_consistency": consistency_findings,
            "attribute_intelligence": attribute_intelligence,
            "linked_jira_ids": base.get("linked_jira_ids") or [],
            "release_readiness": {
                "status": readiness, "blocking_failed_test_evidence": failed,
                "coverage_gap_count": len(gaps), "mapping_issue_count": mapping_issue_count,
                "requirement_code_mismatch_count": len(requirement_mismatches),
                "graph_sync_mismatch_count": len(consistency_findings),
                "rule": "BLOCKED when captured failed test evidence exists; REVIEW_REQUIRED when coverage, requirement/code, mapping, or evidence-consistency gaps exist; otherwise READY_FOR_RELEASE_REVIEW. This is not automatic approval.",
            },
            "evidence_rule": "Regression and release recommendations are derived from current Git/source, registered scenarios, captured baselines, static test evidence, deep-code analysis, mapping evidence and hybrid retrieval discovery. Hybrid retrieval is discovery only; current source wins over stale graph absence and graph/source disagreement is surfaced explicitly.",
        }
