from __future__ import annotations

from pathlib import Path
import re
from typing import Any
from copy import deepcopy

from config import settings
from db_models import JiraKnowledge, Scenario
from baseline_models import ScenarioBaseline, ScenarioReleaseArchive, ScenarioTestBaseline
from services.release_intelligence.regression_release_intelligence_service import RegressionReleaseIntelligenceService
from services.lineage.mapping_intelligence_service import MappingIntelligenceService
from services.jira.jira_change_correlation_service import jira_change_correlation_service


class EngineeringAssistantService:
    """Turn evidence-grounded release intelligence into an actionable developer brief.

    This service deliberately does not invent code relationships. It composes the
    already-grounded regression/release evidence into developer actions. The LLM
    may present this payload through the Knowledge Agent, but the decisions and
    evidence in this payload are deterministic.
    """

    def __init__(self) -> None:
        self.release_intelligence = RegressionReleaseIntelligenceService()

    def status(self) -> dict[str, Any]:
        return {
            "status": "READY",
            "capability": "AI_ENGINEERING_ASSISTANT",
            "project_path": str(Path(settings.JAVA_PROJECT_PATH).resolve()),
            "supports": [
                "CURRENT_CHANGE_EXPLANATION",
                "IMPACT_SUMMARY",
                "REGRESSION_TEST_PLAN",
                "TRACEABILITY_GAP_REVIEW",
                "MAPPING_AND_REQUIREMENT_RISK_REVIEW",
                "MAPPING_DATA_LINEAGE_PROVENANCE",
                "RELEASE_READINESS_GUIDANCE",
                "DEVELOPER_NEXT_ACTIONS",
            ],
            "decision_policy": "EVIDENCE_GROUNDED_NO_AUTOMATIC_APPROVAL",
        }

    @staticmethod
    def _uniq(values: list[str]) -> list[str]:
        return list(dict.fromkeys(v for v in values if v))

    @staticmethod
    def _change_explanation(result: dict[str, Any]) -> list[dict[str, Any]]:
        changes = []
        for item in result.get("behavioral_changes") or []:
            changes.append({
                "type": item.get("type"),
                "attribute": item.get("attribute"),
                "file": item.get("file"),
                "old_condition": item.get("old_condition"),
                "new_condition": item.get("new_condition"),
                "old_operator": item.get("old_operator"),
                "new_operator": item.get("new_operator"),
                "old_threshold": item.get("old_threshold"),
                "new_threshold": item.get("new_threshold"),
                "evidence": item.get("evidence"),
            })
        return changes

    @staticmethod
    def _test_plan(result: dict[str, Any]) -> dict[str, Any]:
        scenario_tests = []
        existing_tests = []
        for rec in result.get("regression_recommendations") or []:
            scenario_tests.append({
                "scenario_code": rec.get("scenario_code"),
                "operation": rec.get("operation"),
                "impact_status": rec.get("impact_status"),
                "recommended_action": rec.get("recommended_action"),
            })
            for evidence in rec.get("automated_test_evidence") or []:
                existing_tests.append({
                    "test_class": evidence.get("test_class"),
                    "test_method": evidence.get("test_method"),
                    "file": evidence.get("file"),
                    "has_assertion": evidence.get("has_assertion"),
                    "evidence_basis": evidence.get("evidence_basis"),
                })
        return {
            "affected_scenario_tests": scenario_tests,
            "existing_test_evidence": existing_tests,
            "boundary_tests_to_add_or_verify": result.get("boundary_regression_recommendations") or [],
        }

    @staticmethod
    def _risks(result: dict[str, Any]) -> list[dict[str, Any]]:
        risks = []
        for gap in result.get("coverage_gaps") or []:
            risks.append({
                "type": gap.get("gap_type"),
                "attribute": gap.get("attribute"),
                "scenario_code": gap.get("scenario_code"),
                "endpoint": gap.get("endpoint"),
                "detail": gap.get("detail"),
            })
        for finding in result.get("evidence_consistency") or []:
            risks.append({
                "type": finding.get("type"),
                "attribute": finding.get("attribute"),
                "target": finding.get("target"),
                "detail": finding,
            })
        return risks

    def _next_actions(self, result: dict[str, Any]) -> list[str]:
        actions: list[str] = []
        summary = result.get("impact_summary") or {}
        readiness = (result.get("release_readiness") or {}).get("status")

        if result.get("boundary_regression_recommendations"):
            actions.append("Add or verify the recommended boundary-value tests for every changed condition.")
        if any((g.get("gap_type") == "NO_CAPTURED_TEST_BASELINE") for g in result.get("coverage_gaps") or []):
            actions.append("Run the affected regression tests and capture a test baseline for each impacted scenario.")
        if int(summary.get("requirement_code_mismatches") or 0) > 0:
            actions.append("Confirm the changed behavior against the authoritative requirement/Jira before release review.")
        if int(summary.get("mapping_quality_issues") or 0) > 0:
            actions.append("Resolve or explicitly review high-severity mapping/contract quality findings.")
        if int(summary.get("graph_sync_mismatches") or 0) > 0:
            actions.append("Re-sync the engineering knowledge graph and re-run the analysis.")
        if int(summary.get("failed_test_baselines") or 0) > 0:
            actions.append("Fix failed captured test evidence before release review.")
        if readiness == "READY_FOR_RELEASE_REVIEW":
            actions.append("Proceed to human release review; this status is not automatic release approval.")
        elif readiness == "NO_CHANGES":
            actions.append("No current Java Git change was detected; make or select a change before requesting change-specific guidance.")
        else:
            actions.append("Re-run Engineering Assistant after the identified evidence gaps are addressed.")
        return self._uniq(actions)


    @staticmethod
    def _class_change_groups(result: dict[str, Any]) -> list[dict[str, Any]]:
        """Group current-change evidence by changed class for IDE presentation."""
        summary = result.get("change_summary") or {}
        methods = summary.get("changed_methods") or []
        attributes = summary.get("changed_attributes") or []
        symbols = summary.get("changed_symbols") or []
        scenarios = result.get("affected_scenarios") or []
        groups = []

        for class_name in summary.get("changed_classes") or []:
            class_methods = [m for m in methods if m.get("class_name") == class_name]
            operations = []
            for scenario in scenarios:
                matched_classes = set(scenario.get("matched_classes") or [])
                matched_methods = set(scenario.get("matched_methods") or [])
                path_text = " ".join(
                    " ".join(str(x) for x in (p.get("path") or []))
                    for p in (scenario.get("dependency_paths") or [])
                )
                applies = (
                    class_name in matched_classes
                    or any(str(m).startswith(class_name + ".") for m in matched_methods)
                    or class_name in path_text
                )
                if applies:
                    operations.append({
                        "scenario_code": scenario.get("scenario_code"),
                        "operation": f"{scenario.get('http_method') or ''} {scenario.get('endpoint') or ''}".strip(),
                        "impact_status": scenario.get("impact_status"),
                        "dependency_paths": scenario.get("dependency_paths") or [],
                    })

            # Attributes are currently reported at change-set scope. Attach only
            # those that appear in a method/path for this class when possible; if
            # evidence cannot be separated safely, keep them visible rather than
            # hiding change evidence from the developer.
            class_attrs = []
            for symbol in symbols:
                if symbol.get("symbol_type") != "ATTRIBUTE" or symbol.get("class_name") != class_name:
                    continue
                raw = str(symbol.get("symbol") or "")
                import re
                match = re.match(r"^(?:get|set|is)([A-Z].*)$", raw)
                if match:
                    raw = match.group(1)
                    raw = raw[:1].lower() + raw[1:]
                if raw and raw not in class_attrs:
                    class_attrs.append(raw)
            if not class_attrs and len(summary.get("changed_classes") or []) == 1:
                class_attrs = list(attributes)

            groups.append({
                "class_name": class_name,
                "changed_methods": class_methods,
                "changed_attributes": class_attrs,
                "affected_operations": operations,
                "impact_count": len(operations),
            })
        return groups


    @staticmethod
    def _jira_requirement_evidence(db, result: dict[str, Any]) -> dict[str, Any]:
        """Resolve authoritative Jira evidence from structured scenario/baseline history.

        Authority order:
        1. Scenario.jira_id (explicit scenario relationship)
        2. ScenarioTestBaseline.jira_ids (captured test-baseline relationship)
        3. Existing regression intelligence linked_jira_ids

        Hybrid-RAG Jira IDs remain discovery-only and are never promoted here.
        """
        confirmed_by_id: dict[str, dict[str, Any]] = {}
        project_path = str(result.get("project_path") or "")

        def add_confirmed(jira_id: Any, evidence: dict[str, Any]) -> None:
            if not jira_id:
                return
            key = str(jira_id).strip()
            if not key:
                return
            entry = confirmed_by_id.setdefault(key, {
                "jira_id": key,
                "relationship": "AUTHORITATIVE_STRUCTURED_LINK",
                "authoritative": True,
                "evidence": [],
            })
            if evidence not in entry["evidence"]:
                entry["evidence"].append(evidence)

        affected = result.get("affected_scenarios") or []
        for affected_scenario in affected:
            scenario_code = affected_scenario.get("scenario_code")
            if not scenario_code:
                continue
            query = db.query(Scenario).filter(Scenario.scenario_code == scenario_code)
            if project_path:
                query = query.filter(Scenario.project_path == project_path)
            scenario = query.first()
            if scenario is None:
                # scenario_code is unique in the current schema; fallback is useful
                # for older rows created before project_path was populated.
                scenario = db.query(Scenario).filter(Scenario.scenario_code == scenario_code).first()
            if scenario is None:
                continue

            operation = f"{scenario.http_method or ''} {scenario.endpoint or ''}".strip()
            if scenario.jira_id:
                add_confirmed(scenario.jira_id, {
                    "source": "SCENARIO",
                    "scenario_code": scenario.scenario_code,
                    "operation": operation,
                    "relationship": "Scenario.jira_id",
                })

            test_baselines = (
                db.query(ScenarioTestBaseline)
                .filter(ScenarioTestBaseline.scenario_id == scenario.id)
                .order_by(ScenarioTestBaseline.created_at.desc(), ScenarioTestBaseline.id.desc())
                .all()
            )
            for test in test_baselines:
                jira_ids = test.jira_ids or []
                if isinstance(jira_ids, str):
                    jira_ids = [jira_ids]
                release_baseline = None
                if test.baseline_id:
                    release_baseline = db.query(ScenarioBaseline).filter(ScenarioBaseline.id == test.baseline_id).first()
                archive = None
                if test.baseline_id:
                    archive = db.query(ScenarioReleaseArchive).filter(ScenarioReleaseArchive.baseline_id == test.baseline_id).first()
                for jira_id in jira_ids:
                    add_confirmed(jira_id, {
                        "source": "SCENARIO_TEST_BASELINE",
                        "scenario_code": scenario.scenario_code,
                        "operation": operation,
                        "test_baseline_id": test.id,
                        "test_baseline_name": test.baseline_name,
                        "test_status": test.status,
                        "code_baseline_version": test.code_baseline_version,
                        "release_baseline_id": test.baseline_id,
                        "release_name": getattr(release_baseline, "baseline_name", None),
                        "release_version": getattr(release_baseline, "release_version", None),
                        "archived": archive is not None,
                        "relationship": "Scenario → TestBaseline → JIRA",
                    })

        for jira_id in result.get("linked_jira_ids") or []:
            add_confirmed(jira_id, {
                "source": "REGRESSION_RELEASE_INTELLIGENCE",
                "relationship": "linked_jira_ids",
            })

        confirmed = list(confirmed_by_id.values())
        for entry in confirmed:
            knowledge = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == entry["jira_id"]).first()
            if knowledge is not None:
                entry["title"] = knowledge.title
                entry["requirement"] = knowledge.requirement
                entry["jira_knowledge_source"] = "JiraKnowledge structured record"

        confirmed_ids = set(confirmed_by_id)
        discovered: list[dict[str, Any]] = []
        seen_discovered: set[tuple[str, str]] = set()
        for intelligence in result.get("attribute_intelligence") or []:
            related = intelligence.get("related_knowledge") or {}
            retrieval = related.get("retrieval") or {}
            for doc in retrieval.get("results") or []:
                metadata = doc.get("metadata") or {}
                extracted = metadata.get("extracted") or {}
                for jira_id in extracted.get("jira_ids") or []:
                    key = (str(jira_id), str(doc.get("document_id") or doc.get("title") or ""))
                    if not jira_id or key in seen_discovered or str(jira_id) in confirmed_ids:
                        continue
                    seen_discovered.add(key)
                    discovered.append({
                        "jira_id": str(jira_id),
                        "source_type": doc.get("source_type"),
                        "source_title": doc.get("title"),
                        "document_id": doc.get("document_id"),
                        "evidence": "HYBRID_RAG_DISCOVERY_ONLY",
                        "authoritative": False,
                        "note": "Discovery evidence only; not promoted to an authoritative Jira relationship.",
                    })

        requirements = []
        for mismatch in result.get("requirement_code_mismatches") or []:
            requirements.append({
                "attribute": mismatch.get("attribute"),
                "severity": mismatch.get("severity"),
                "source_type": mismatch.get("document_source_type"),
                "source_title": mismatch.get("document_title"),
                "documented_rule": f"{mismatch.get('documented_operator') or ''}{mismatch.get('documented_threshold')}",
                "current_code_rule": f"{mismatch.get('current_code_operator') or ''}{mismatch.get('current_code_threshold')}",
                "current_code_file": mismatch.get("current_code_file"),
                "evidence_rule": mismatch.get("evidence_rule"),
            })

        correlation = jira_change_correlation_service.correlate(
            db,
            result,
            historical_jira_ids=confirmed_ids,
        )

        return {
            "impacted_historical_jiras": confirmed,
            "confirmed_jiras": confirmed,  # compatibility for older plugin builds
            "current_change_jira_candidates": correlation.get("candidates") or [],
            "current_change_jira_correlation": correlation,
            "discovered_related_jiras": discovered,
            "requirements": requirements,
            "evidence_rule": (
                "Impacted/historical Jira links come only from structured Scenario/ScenarioTestBaseline/release evidence. "
                "They prove historical traceability for an affected scenario; they do not prove that the current uncommitted change belongs to that Jira. "
                "Code + test evidence is also correlated against Jira knowledge to identify likely current-change candidates. "
                "Those candidates remain non-authoritative until current-change/test-baseline evidence confirms them. "
                "Hybrid-RAG Jira IDs remain discovery-only until a structured relationship confirms them."
            ),
        }

    @staticmethod
    def _risk_explanation(risk: dict[str, Any]) -> dict[str, Any]:
        gap_type = risk.get("gap_type") or risk.get("type") or "ENGINEERING_RISK"
        scenario = risk.get("scenario_code")
        endpoint = risk.get("endpoint")
        attribute = risk.get("attribute")
        detail = risk.get("detail")
        why = {
            "NO_CAPTURED_TEST_BASELINE": "Current code impact reaches this scenario, but no captured test baseline is linked to the affected scenario.",
            "NO_AUTOMATED_TEST_SOURCE_EVIDENCE": "The affected scenario/code path was found, but no matching automated test-source evidence was found for the changed flow.",
            "MAPPING_QUALITY_RISK": "The changed attribute participates in mapping/contract definitions with a quality finding that can alter runtime data behavior.",
            "REQUIREMENT_CODE_MISMATCH": "Current source behavior differs from discovered requirement/release documentation and requires authoritative requirement/Jira review.",
            "BEHAVIORAL_BOUNDARY_COVERAGE_REQUIRED": "A numeric condition changed, so values immediately below, at, and above the new boundary must be verified.",
        }.get(str(gap_type), "The current change produced an evidence gap or consistency finding that requires review.")
        return {
            "type": gap_type,
            "severity": (detail.get("severity") if isinstance(detail, dict) else None),
            "attribute": attribute,
            "scenario_code": scenario,
            "endpoint": endpoint,
            "why": why,
            "evidence": {
                "scenario_code": scenario,
                "endpoint": endpoint,
                "attribute": attribute,
                "detail": detail,
            },
            "recommended_action": {
                "NO_CAPTURED_TEST_BASELINE": "Run the affected scenario regression test and capture/link its test baseline.",
                "NO_AUTOMATED_TEST_SOURCE_EVIDENCE": "Add or identify automated test coverage for the affected code path.",
                "MAPPING_QUALITY_RISK": "Review the mapping provenance and resolve or explicitly accept the conflicting/invalid definition.",
                "REQUIREMENT_CODE_MISMATCH": "Confirm the intended behavior against the authoritative Jira/requirement before release review.",
                "BEHAVIORAL_BOUNDARY_COVERAGE_REQUIRED": "Add or verify the recommended boundary-value tests.",
            }.get(str(gap_type), "Review the evidence and resolve the gap before release review."),
        }

    @classmethod
    def _explained_risks(cls, result: dict[str, Any]) -> list[dict[str, Any]]:
        items = [cls._risk_explanation(g) for g in (result.get("coverage_gaps") or [])]
        for finding in result.get("evidence_consistency") or []:
            items.append(cls._risk_explanation(finding))
        return items


    @staticmethod
    def _reconcile_captured_baseline_evidence(db, result: dict[str, Any]) -> dict[str, Any]:
        """Remove false NO_CAPTURED_TEST_BASELINE gaps using structured baseline truth.

        A historical/captured baseline proves that a baseline exists for the affected
        scenario. It does not prove that the newly changed behavior is covered.
        Boundary/behavioral coverage findings therefore remain untouched.
        """
        reconciled = deepcopy(result)
        project_path = str(reconciled.get("project_path") or "")
        captured_by_scenario: dict[str, list[dict[str, Any]]] = {}

        for affected in reconciled.get("affected_scenarios") or []:
            scenario_code = affected.get("scenario_code")
            if not scenario_code:
                continue
            query = db.query(Scenario).filter(Scenario.scenario_code == scenario_code)
            if project_path:
                query = query.filter(Scenario.project_path == project_path)
            scenario = query.first()
            if scenario is None:
                scenario = db.query(Scenario).filter(Scenario.scenario_code == scenario_code).first()
            if scenario is None:
                continue
            tests = (
                db.query(ScenarioTestBaseline)
                .filter(ScenarioTestBaseline.scenario_id == scenario.id)
                .order_by(ScenarioTestBaseline.created_at.desc(), ScenarioTestBaseline.id.desc())
                .all()
            )
            if not tests:
                continue
            captured_by_scenario[scenario_code] = [{
                "test_baseline_id": t.id,
                "test_baseline_name": t.baseline_name,
                "status": t.status,
                "code_baseline_version": t.code_baseline_version,
                "release_baseline_id": t.baseline_id,
                "jira_ids": t.jira_ids or [],
            } for t in tests]

        original_gaps = reconciled.get("coverage_gaps") or []
        kept = []
        reconciled_items = []
        for gap in original_gaps:
            scenario_code = gap.get("scenario_code")
            if gap.get("gap_type") == "NO_CAPTURED_TEST_BASELINE" and scenario_code in captured_by_scenario:
                reconciled_items.append({
                    "removed_gap_type": "NO_CAPTURED_TEST_BASELINE",
                    "scenario_code": scenario_code,
                    "reason": "Structured ScenarioTestBaseline evidence exists for this affected scenario.",
                    "captured_baselines": captured_by_scenario[scenario_code],
                    "coverage_note": "Historical baseline existence does not prove coverage of the newly changed behavior.",
                })
                continue
            kept.append(gap)
        reconciled["coverage_gaps"] = kept
        reconciled["captured_test_baseline_evidence"] = captured_by_scenario
        reconciled["evidence_reconciliation"] = reconciled_items

        summary = reconciled.get("impact_summary") or {}
        if "coverage_gaps" in summary:
            summary["coverage_gaps"] = len(kept)
        readiness = reconciled.get("release_readiness") or {}
        if "coverage_gap_count" in readiness:
            readiness["coverage_gap_count"] = len(kept)
        return reconciled

    def _mapping_data_lineage(self, db, result: dict[str, Any]) -> dict[str, Any]:
        """Resolve where changed values originate using authoritative mapping rows.

        Mapping definitions remain the source of truth. RAG is not used to invent
        lineage. Every returned mapping keeps document/version/sheet/row provenance.
        """
        summary = result.get("change_summary") or {}
        attributes = list(summary.get("changed_attributes") or [])
        for change in result.get("behavioral_changes") or []:
            attribute = str(change.get("attribute") or "").strip()
            if attribute and attribute not in attributes:
                attributes.append(attribute)

        service = MappingIntelligenceService()
        evidence = []
        for attribute in self._uniq(attributes):
            try:
                lineage = service.lineage(db, attribute)
            except Exception as exc:
                evidence.append({
                    "attribute": attribute,
                    "status": "UNAVAILABLE",
                    "mappings": [],
                    "error": str(exc),
                })
                continue

            mappings = lineage.get("mappings") or []
            conflicts = lineage.get("conflicts") or []

            # Classify mapping evidence by mapping-family/version so the plugin can
            # distinguish the latest definition from older history.  A conflict is
            # evidence, not silently resolved.  Mapping quality remains authoritative
            # for targets that do not exist in the current source graph.
            def version_key(value: Any) -> tuple[int, ...]:
                nums = re.findall(r"\d+", str(value or ""))
                return tuple(int(n) for n in nums) if nums else (0,)

            latest_by_family: dict[str, str] = {}
            for mapping in mappings:
                provenance = mapping.get("provenance") or {}
                family = str(provenance.get("mapping_family") or provenance.get("document") or "UNKNOWN")
                version = str(provenance.get("document_version") or "UNSPECIFIED")
                current = latest_by_family.get(family)
                if current is None or version_key(version) > version_key(current):
                    latest_by_family[family] = version

            conflict_ids = {
                int(definition.get("id"))
                for conflict in conflicts
                for definition in (conflict.get("definitions") or [])
                if definition.get("id") is not None
            }
            missing_target_ids: set[int] = set()
            for item in result.get("attribute_intelligence") or []:
                if str(item.get("attribute") or "").lower() != attribute.lower():
                    continue
                for issue in ((item.get("mapping_quality") or {}).get("issues") or []):
                    issue_type = str(issue.get("type") or issue.get("issue_type") or "").upper()
                    if "TARGET_NOT_FOUND" not in issue_type:
                        continue
                    for definition in issue.get("definitions") or []:
                        definition_id = definition.get("id")
                        if definition_id is None:
                            definition_id = definition.get("mapping_id")
                        if definition_id is not None:
                            missing_target_ids.add(int(definition_id))

            classified = []
            for mapping in mappings:
                item = dict(mapping)
                provenance = item.get("provenance") or {}
                family = str(provenance.get("mapping_family") or provenance.get("document") or "UNKNOWN")
                version = str(provenance.get("document_version") or "UNSPECIFIED")
                mapping_id = item.get("id")
                version_status = "LATEST" if version == latest_by_family.get(family) else "HISTORICAL"
                target_status = (
                    "TARGET_NOT_FOUND"
                    if mapping_id is not None and int(mapping_id) in missing_target_ids
                    else "TARGET_EXISTS"
                )
                conflict_status = (
                    "CONFLICTING"
                    if mapping_id is not None and int(mapping_id) in conflict_ids
                    else "NO_CONFLICT_DETECTED"
                )

                # Version recency and code validity are deliberately independent.
                # The newest mapping is never called matching when its Java target
                # does not exist in the current code graph.
                if target_status == "TARGET_NOT_FOUND":
                    classification = "LATEST_BUT_INVALID" if version_status == "LATEST" else "HISTORICAL_INVALID"
                elif conflict_status == "CONFLICTING":
                    classification = "LATEST_CONFLICTING" if version_status == "LATEST" else "HISTORICAL_CONFLICTING"
                elif version_status == "LATEST":
                    classification = "LATEST_VALID"
                else:
                    classification = "HISTORICAL_VALID"

                item["classification"] = classification
                item["version_status"] = version_status
                item["target_status"] = target_status
                item["conflict_status"] = conflict_status
                item["mapping_family"] = family
                item["latest_family_version"] = latest_by_family.get(family)
                classified.append(item)

            evidence.append({
                "attribute": attribute,
                "status": "FOUND" if mappings else "NOT_FOUND",
                "mapping_count": len(classified),
                "mappings": classified,
                "conflicts": conflicts,
                "latest_versions_by_family": latest_by_family,
                "evidence_rule": lineage.get("evidence_rule"),
            })

        return {
            "attributes": evidence,
            "evidence_rule": (
                "Mapping lineage is resolved from authoritative imported mapping definitions. "
                "Every mapping preserves source type/system/path, transformation/default/validation, "
                "target, and document/version/sheet/row provenance."
            ),
        }

    def analyse_current_change(self, db) -> dict[str, Any]:
        result = self._reconcile_captured_baseline_evidence(db, self.release_intelligence.analyse(db))
        change_summary = result.get("change_summary") or {}
        readiness = result.get("release_readiness") or {}

        affected_operations = []
        for scenario in result.get("affected_scenarios") or []:
            affected_operations.append({
                "scenario_code": scenario.get("scenario_code"),
                "operation": f"{scenario.get('http_method') or ''} {scenario.get('endpoint') or ''}".strip(),
                "impact_status": scenario.get("impact_status"),
                "dependency_paths": scenario.get("dependency_paths") or [],
            })

        return {
            "status": "ANALYZED",
            "capability": "AI_ENGINEERING_ASSISTANT",
            "project_path": result.get("project_path"),
            "developer_brief": {
                "changed_files": change_summary.get("changed_java_files", 0),
                "changed_classes": change_summary.get("changed_classes") or [],
                "changed_methods": change_summary.get("changed_methods") or [],
                "changed_attributes": change_summary.get("changed_attributes") or [],
                "behavioral_changes": self._change_explanation(result),
                "affected_operations": affected_operations,
                "class_change_groups": self._class_change_groups(result),
            },
            "test_plan": {**self._test_plan(result), "captured_test_baselines": result.get("captured_test_baseline_evidence") or {}},
            "requirement_code_mismatches": result.get("requirement_code_mismatches") or [],
            "engineering_risks": self._explained_risks(result),
            "jira_requirement_evidence": self._jira_requirement_evidence(db, result),
            "mapping_data_lineage": self._mapping_data_lineage(db, result),
            "linked_jira_ids": result.get("linked_jira_ids") or [],
            "evidence_reconciliation": result.get("evidence_reconciliation") or [],
            "release_readiness": readiness,
            "developer_next_actions": self._next_actions(result),
            "evidence_summary": result.get("impact_summary") or {},
            "evidence_rule": (
                "The Engineering Assistant summarizes deterministic current-change evidence. "
                "It does not create code relationships, Jira links, test results or release approval. "
                "Hybrid retrieval remains discovery evidence and release readiness remains a review gate."
            ),
        }
