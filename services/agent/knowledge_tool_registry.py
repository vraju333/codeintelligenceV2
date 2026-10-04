from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from sqlalchemy.orm import Session

from services.knowledge.common_entity_extractor import CommonKnowledgeEntityExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService
from services.knowledge.unified_knowledge_search_service import UnifiedKnowledgeSearchService
from services.lineage.attribute_impact_service import AttributeImpactService
from services.regression.git_diff_service import GitDiffService
from services.test_analysis.test_code_analysis_service import TestCodeAnalysisService
from baseline_models import ScenarioBaseline, ScenarioTestBaseline, ScenarioReleaseArchive


class KnowledgeToolRegistry:
    """Build the tools exposed to the knowledge agent for one DB request."""

    def __init__(self, db: Session):
        self.db = db
        self.knowledge = KnowledgeIngestionService()
        self.unified = UnifiedKnowledgeSearchService()
        self.extractor = CommonKnowledgeEntityExtractor()

    def tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.rag_search,
                name="rag_search",
                description=(
                    "Semantic search over ingested engineering knowledge documents. "
                    "Use when the user describes a concept in natural language or asks "
                    "which documents discuss a topic and no exact engineering ID is required."
                ),
            ),
            StructuredTool.from_function(
                func=self.graph_search,
                name="graph_search",
                description=(
                    "Expand relationships for known engineering entities such as a JIRA ID, "
                    "attribute, scenario, class, method, test, commit, file, endpoint or release. "
                    "Best when the user asks what is connected/related to a known entity."
                ),
            ),
            StructuredTool.from_function(
                func=self.unified_knowledge_search,
                name="unified_knowledge_search",
                description=(
                    "Return complete cross-source engineering knowledge for a topic by combining "
                    "semantic RAG discovery, Neo4j relationship expansion and PostgreSQL evidence. "
                    "Use for broad requests such as complete impact, everything about a JIRA, "
                    "or a consolidated requirement/test/code-change view."
                ),
            ),
            StructuredTool.from_function(
                func=self.static_code_analysis,
                name="static_code_analysis",
                description=(
                    "Analyze the current source-code blast radius of an attribute using CodeIntelligence "
                    "static analysis. Returns occurrences, impacted classes, direct methods, layers and "
                    "affected HTTP endpoints. Use for questions about what current code is impacted by "
                    "changing an attribute such as gpa, primaryEmail or countryCode."
                ),
            ),
            StructuredTool.from_function(
                func=self.scenario_impact,
                name="scenario_impact",
                description=(
                    "Find active CodeIntelligence scenarios affected by an attribute. Returns scenario "
                    "codes, operations, endpoints, relevance evidence and scores. Use when the user asks "
                    "which scenarios or operations should be considered/regression tested for an attribute."
                ),
            ),
            StructuredTool.from_function(
                func=self.git_scenario_impact,
                name="git_scenario_impact",
                description=(
                    "Analyze CURRENT Git structural business-attribute changes and correlate them with active "
                    "CodeIntelligence scenarios. Use when the user asks which scenarios, endpoints or operations "
                    "are impacted by current/uncommitted/staged Git changes or what should be regression tested. "
                    "Returns deduplicated scenario evidence with the changed attributes that caused each match."
                ),
            ),
            StructuredTool.from_function(
                func=self.git_sdlc_traceability,
                name="git_sdlc_traceability",
                description=(
                    "Close the CURRENT Git change analysis across the SDLC in one call. Correlates current Git "
                    "changes to confirmed requirements/JIRAs, impacted scenarios, static JUnit evidence, captured "
                    "test baselines, release baselines, archives and current Git branch/HEAD. Use for end-to-end "
                    "traceability, change closure, regression evidence, baseline readiness, or questions spanning "
                    "Git -> requirement/JIRA -> scenario -> test -> baseline -> commit/release history."
                ),
            ),
            StructuredTool.from_function(
                func=self.baseline_history,
                name="baseline_history",
                description=(
                    "Find historical release/test-baseline traceability for an attribute from PostgreSQL "
                    "baseline history. Returns releases, baseline versions, test scenarios/status and linked "
                    "JIRAs. Use for questions about how an attribute was tested previously or release history."
                ),
            ),
            StructuredTool.from_function(
                func=self.git_change_analysis,
                name="git_change_analysis",
                description=(
                    "Analyze the CURRENT Git working-tree/staged Java changes for the active project. "
                    "Returns changed files, classes, methods, attributes and normalized source changes."
                ),
            ),
            StructuredTool.from_function(
                func=self.git_requirement_correlation,
                name="git_requirement_correlation",
                description=(
                    "Correlate CURRENT Git structural attribute changes with requirements and live JIRAs. "
                    "This is the authoritative tool for asking which requirements/JIRAs relate to current Git changes. "
                    "It rejects semantic-only matches and returns confirmed evidence separately from candidates."
                ),
            ),
        ]

    def git_change_analysis(self) -> str:
        """Return clean, normalized current Git changes without sending raw source code to the LLM."""
        result = GitDiffService().analyse_changes()

        changed_files = []
        structural_attributes = set()
        changed_classes = set()
        changed_methods = set()
        total_detected_changes = 0

        # Only these structural changes are strong evidence that a business/data attribute
        # itself changed. Identifiers merely referenced inside modified methods must not
        # become "changed_attributes".
        attribute_change_types = {
            "FIELD_ADDED",
            "FIELD_REMOVED",
            "FIELD_DELETED",
            "FIELD_TYPE_CHANGED",
            "FIELD_RENAMED",
            "FIELD_MODIFIED",
        }

        for item in result.get("changed_files", []) or []:
            class_name = str(item.get("class_name") or "").strip()
            if class_name:
                changed_classes.add(class_name)

            methods = []
            for method in item.get("changed_methods", []) or []:
                method_name = str(method.get("method_name") or "").strip()
                if not method_name:
                    continue
                qualified = f"{class_name}.{method_name}" if class_name else method_name
                changed_methods.add(qualified)
                methods.append({
                    "method_name": method_name,
                    "qualified_name": qualified,
                    "changed_lines": method.get("changed_lines") or [],
                })

            structural_changes = []
            for change in item.get("source_changes", []) or []:
                total_detected_changes += 1
                change_type = str(change.get("change_type") or "").upper()
                symbol = str(change.get("symbol") or "").strip()

                normalized = {
                    "change_type": change_type,
                    "label": change.get("label"),
                    "symbol": symbol,
                    "data_type": change.get("data_type"),
                    "line_number": change.get("line_number"),
                    "business_attribute_candidate": bool(change.get("business_attribute_candidate")),
                }

                if (
                    change_type in attribute_change_types
                    and symbol
                    and bool(change.get("business_attribute_candidate"))
                ):
                    structural_attributes.add(symbol)
                    structural_changes.append(normalized)

            # Keep changed_symbols for diagnostics, but never derive business attributes
            # from generic ATTRIBUTE tokens inside method bodies.
            symbols = []
            for symbol in item.get("changed_symbols", []) or []:
                symbols.append({
                    "symbol_type": symbol.get("symbol_type"),
                    "symbol": symbol.get("symbol"),
                    "class_name": symbol.get("class_name"),
                    "change_type": symbol.get("change_type"),
                    "line_number": symbol.get("line_number"),
                })

            changed_files.append({
                "file_path": item.get("file_path"),
                "file_name": item.get("file_name"),
                "class_name": class_name,
                "changed_lines": item.get("changed_lines") or [],
                "changed_methods": methods,
                "structural_attribute_changes": structural_changes,
                "changed_symbols": symbols,
            })

        payload = {
            "project_path": result.get("project_path"),
            "git_root": result.get("git_root"),
            "total_changed_java_files": result.get("total_changed_java_files", len(changed_files)),
            "total_detected_changes": total_detected_changes,
            "changed_attributes": sorted(structural_attributes),
            "changed_classes": sorted(changed_classes),
            "changed_methods": sorted(changed_methods),
            "changed_files": changed_files,
            "knowledge_search_hints": sorted(structural_attributes),
            "analysis_basis": {
                "source": "git_working_tree_and_staged_changes",
                "attribute_filter": "semantic_structural_business_fields_only",
                "local_only": True,
                "llm_used_for_git_analysis": False,
                "raw_source_diff_sent_external": False,
            },
        }
        return json.dumps(payload, default=str)

    def git_requirement_correlation(self) -> str:
        """Correlate current structural Git changes to explicit requirement/JIRA evidence."""
        git_payload = json.loads(self.git_change_analysis())
        attributes = git_payload.get("changed_attributes") or []
        correlations = []
        confirmed_jiras = set()
        confirmed_requirements = []

        for attribute in attributes:
            relation = self.unified.correlate_concept(self.db, attribute, top_k=8)
            for evidence in relation.get("confirmed_evidence", []) or []:
                source_type = str(evidence.get("source_type") or "").upper()
                source_ref = str(evidence.get("source_ref") or "").strip()
                if source_type == "JIRA" and source_ref:
                    confirmed_jiras.add(source_ref.upper())
                elif source_type == "REQUIREMENT":
                    confirmed_requirements.append({
                        "attribute": attribute,
                        "title": evidence.get("title"),
                        "source_ref": source_ref,
                    })
            correlations.append(relation)

        payload = {
            "git_changes": {
                "project_path": git_payload.get("project_path"),
                "git_root": git_payload.get("git_root"),
                "changed_attributes": attributes,
                "changed_classes": git_payload.get("changed_classes") or [],
                "changed_methods": git_payload.get("changed_methods") or [],
                "changed_files": git_payload.get("changed_files") or [],
            },
            "correlations": correlations,
            "confirmed_jiras": sorted(confirmed_jiras),
            "confirmed_requirements": confirmed_requirements,
            "rule": (
                "Only explicit entity matches are confirmed. Semantic-only RAG/graph hits are candidates "
                "and must not be reported as related requirements or JIRAs."
            ),
            "analysis_basis": {
                "git_local_only": True,
                "raw_source_diff_sent_external": False,
                "semantic_similarity_establishes_relationship": False,
            },
        }
        return json.dumps(payload, default=str)

    def rag_search(self, query: str, top_k: int = 5) -> str:
        """Search engineering knowledge semantically."""
        result = self.knowledge.search(self.db, query, top_k=max(1, min(int(top_k), 10)))
        return json.dumps(result, default=str)

    def graph_search(self, entity: str) -> str:
        """Find knowledge documents and entities connected to an exact engineering entity."""
        direct = self.extractor.extract(str(entity or ""), "REQUIREMENT")
        seeds = {field: [] for field in self.unified.ENTITY_FIELDS}
        self.unified._merge_entities(seeds, direct)

        # If the generic extractor cannot classify a value (for example a method
        # name), seed all entity fields with it. Neo4j still performs an exact
        # entity-name match, so this does not broaden the graph result incorrectly.
        if not self.unified._entity_values(seeds):
            value = str(entity or "").strip()
            if value:
                for field in self.unified.ENTITY_FIELDS:
                    seeds[field] = [value]

        graph = self.unified._expand_graph(seeds)
        rows = self.unified._project_rows(self.db)
        ids = set(graph.get("document_ids") or [])
        evidence = [self.unified._evidence(row) for row in rows if row.id in ids]
        return json.dumps({"entity": entity, "graph": graph, "evidence": evidence}, default=str)

    def unified_knowledge_search(self, query: str, top_k: int = 5) -> str:
        """Search all connected knowledge and return consolidated evidence."""
        result = self.unified.search(self.db, query, top_k=max(1, min(int(top_k), 10)))
        return json.dumps(result, default=str)


    def static_code_analysis(self, attribute: str) -> str:
        """Analyze current source-code impact for an attribute without invoking an LLM."""
        result = AttributeImpactService()._analyze_code_only(str(attribute or "").strip(), self.db)
        payload = {
            "attribute": result.get("attribute"),
            "total_occurrences": result.get("total_occurrences"),
            "impacted_classes": result.get("impacted_classes") or [],
            "direct_attribute_methods": result.get("direct_attribute_methods") or [],
            "layers": result.get("layers") or [],
            "occurrences": result.get("occurrences") or [],
            "affected_endpoints": result.get("affected_endpoints") or [],
            "shared_component_impact": result.get("shared_component_impact") or [],
            "confidence": result.get("confidence") or {},
            "analysis_basis": result.get("analysis_basis") or {},
        }
        return json.dumps(payload, default=str)

    def scenario_impact(self, attribute: str) -> str:
        """Find scenarios/operations affected by an attribute using existing impact analysis."""
        result = AttributeImpactService()._analyze_code_only(str(attribute or "").strip(), self.db)
        payload = {
            "attribute": result.get("attribute"),
            "affected_scenarios": result.get("affected_scenarios") or [],
            "shared_component_impact": result.get("shared_component_impact") or [],
            "domain_filter": result.get("domain_filter") or {},
            "confidence": result.get("confidence") or {},
        }
        return json.dumps(payload, default=str)

    def git_scenario_impact(self) -> str:
        """Correlate current Git structural business changes with active scenarios."""
        git_payload = json.loads(self.git_change_analysis())
        attributes = git_payload.get("changed_attributes") or []

        scenario_map: dict[str, dict[str, Any]] = {}
        attribute_results = []

        for attribute in attributes:
            impact = AttributeImpactService()._analyze_code_only(attribute, self.db)
            affected = impact.get("affected_scenarios") or []

            attribute_results.append({
                "attribute": attribute,
                "confidence": impact.get("confidence") or {},
                "affected_scenario_count": len(affected),
                "affected_scenarios": affected,
            })

            for scenario in affected:
                scenario_id = scenario.get("id")
                scenario_code = str(scenario.get("scenario_code") or "").strip()
                http_method = str(scenario.get("http_method") or "").upper()
                endpoint = str(scenario.get("endpoint") or "").strip()
                key = str(scenario_id) if scenario_id is not None else f"{scenario_code}|{http_method}|{endpoint}"

                item = scenario_map.setdefault(key, {
                    "id": scenario_id,
                    "scenario_code": scenario_code,
                    "scenario_name": scenario.get("scenario_name"),
                    "http_method": http_method,
                    "endpoint": endpoint,
                    "relevance": scenario.get("relevance"),
                    "score": int(scenario.get("score") or 0),
                    "changed_attributes": [],
                    "reasons": [],
                    "matched_classes": [],
                    "matched_methods": [],
                    "dependency_paths": [],
                })

                if attribute not in item["changed_attributes"]:
                    item["changed_attributes"].append(attribute)

                candidate_score = int(scenario.get("score") or 0)
                if candidate_score > item["score"]:
                    item["score"] = candidate_score
                    item["relevance"] = scenario.get("relevance")

                for reason in scenario.get("reasons") or []:
                    if reason not in item["reasons"]:
                        item["reasons"].append(reason)
                for class_name in scenario.get("matched_classes") or []:
                    if class_name not in item["matched_classes"]:
                        item["matched_classes"].append(class_name)
                for method_name in scenario.get("matched_methods") or []:
                    if method_name not in item["matched_methods"]:
                        item["matched_methods"].append(method_name)
                for path in scenario.get("dependency_paths") or []:
                    if path not in item["dependency_paths"]:
                        item["dependency_paths"].append(path)

        impacted = list(scenario_map.values())
        relevance_rank = {"DIRECT": 0, "TRANSITIVE": 1, "SHARED_FLOW": 2, "SHARED FLOW": 2}
        impacted.sort(key=lambda item: (
            relevance_rank.get(str(item.get("relevance") or "").upper(), 9),
            -int(item.get("score") or 0),
            str(item.get("scenario_code") or ""),
        ))

        payload = {
            "git_changes": {
                "changed_attributes": attributes,
                "changed_classes": git_payload.get("changed_classes") or [],
                "changed_methods": git_payload.get("changed_methods") or [],
                "total_changed_java_files": git_payload.get("total_changed_java_files") or 0,
            },
            "impacted_scenarios": impacted,
            "impacted_scenario_count": len(impacted),
            "attribute_results": attribute_results,
            "regression_recommendation": [
                {
                    "scenario_code": item.get("scenario_code"),
                    "scenario_name": item.get("scenario_name"),
                    "http_method": item.get("http_method"),
                    "endpoint": item.get("endpoint"),
                    "relevance": item.get("relevance"),
                    "changed_attributes": item.get("changed_attributes") or [],
                }
                for item in impacted
            ],
            "analysis_basis": {
                "git_local_only": True,
                "scenario_source": "existing_attribute_impact_analysis",
                "llm_used_for_impact_detection": False,
                "raw_source_diff_sent_external": False,
            },
        }
        return json.dumps(payload, default=str)

    def git_sdlc_traceability(self) -> str:
        """Build evidence-only SDLC lineage for the current Git working tree."""
        requirement = json.loads(self.git_requirement_correlation())
        scenario_impact = json.loads(self.git_scenario_impact())
        git_changes = requirement.get("git_changes") or {}
        impacted = scenario_impact.get("impacted_scenarios") or []
        confirmed_jiras = {
            str(x or "").strip().upper()
            for x in (requirement.get("confirmed_jiras") or []) if x
        }

        test_scanner = TestCodeAnalysisService()
        test_analysis = test_scanner.analyse()
        changed_classes = git_changes.get("changed_classes") or []
        changed_attributes = git_changes.get("changed_attributes") or []

        # Component evidence is intentionally global. It proves that a current-Git
        # changed production component/attribute has a static JUnit, but it must
        # never be copied into every scenario or treated as endpoint-flow coverage.
        component_junit_evidence = test_scanner.evidence_for_changed_components(
            changed_classes, changed_attributes, test_analysis
        )

        scenario_lineage = []
        all_flow_test_evidence = []
        current_test_baselines = []
        historical_test_baselines = []
        current_release_baselines = []
        historical_release_baselines = []
        gaps = []

        for scenario in impacted:
            scenario_id = scenario.get("id")
            if scenario_id is None:
                continue

            # Only executable-flow matches are scenario-level JUnit evidence.
            flow_junit_evidence = test_scanner.evidence_for_scenario(scenario, test_analysis)
            for evidence in flow_junit_evidence:
                tagged = dict(evidence)
                tagged["evidence_scope"] = "SCENARIO_FLOW_STATIC_JUNIT"
                tagged["scenario_id"] = scenario_id
                tagged["scenario_code"] = scenario.get("scenario_code")
                all_flow_test_evidence.append(tagged)

            baselines = (
                self.db.query(ScenarioBaseline)
                .filter(ScenarioBaseline.scenario_id == int(scenario_id))
                .order_by(ScenarioBaseline.baseline_version.desc())
                .all()
            )
            test_baselines = (
                self.db.query(ScenarioTestBaseline)
                .filter(ScenarioTestBaseline.scenario_id == int(scenario_id))
                .order_by(ScenarioTestBaseline.created_at.desc(), ScenarioTestBaseline.id.desc())
                .all()
            )
            archives = (
                self.db.query(ScenarioReleaseArchive)
                .filter(ScenarioReleaseArchive.scenario_id == int(scenario_id))
                .order_by(ScenarioReleaseArchive.archived_at.desc())
                .all()
            )

            release_rows = []
            release_by_id = {}
            for baseline in baselines:
                release = {
                    "baseline_id": baseline.id,
                    "release_name": baseline.baseline_name,
                    "release_version": baseline.release_version,
                    "code_baseline_version": baseline.baseline_version,
                    "is_active": bool(baseline.is_active),
                    "created_at": baseline.created_at,
                    "archived": any(a.baseline_id == baseline.id for a in archives),
                }
                release_rows.append(release)
                release_by_id[baseline.id] = release

            current_rows = []
            historical_rows = []
            current_release_ids = set()
            for test in test_baselines:
                test_jiras = {
                    str(x or "").strip().upper()
                    for x in (test.jira_ids or []) if x
                }
                is_current = bool(confirmed_jiras and (test_jiras & confirmed_jiras))
                row = {
                    "test_baseline_id": test.id,
                    "name": test.baseline_name,
                    "status": test.status,
                    "jira_ids": test.jira_ids or [],
                    "baseline_id": test.baseline_id,
                    "code_baseline_version": test.code_baseline_version,
                    "created_at": test.created_at,
                    "evidence_scope": "CURRENT_CHANGE" if is_current else "HISTORICAL_CONTEXT",
                }
                tagged = {"scenario_code": scenario.get("scenario_code"), **row}
                if is_current:
                    current_rows.append(row)
                    current_test_baselines.append(tagged)
                    if test.baseline_id is not None:
                        current_release_ids.add(test.baseline_id)
                else:
                    historical_rows.append(row)
                    historical_test_baselines.append(tagged)

            scenario_current_releases = []
            scenario_historical_releases = []
            for release in release_rows:
                tagged = {"scenario_code": scenario.get("scenario_code"), **release}
                if release.get("baseline_id") in current_release_ids:
                    current = dict(release)
                    current["evidence_scope"] = "CURRENT_CHANGE"
                    scenario_current_releases.append(current)
                    current_release_baselines.append({"scenario_code": scenario.get("scenario_code"), **current})
                else:
                    historical = dict(release)
                    historical["evidence_scope"] = "HISTORICAL_CONTEXT"
                    scenario_historical_releases.append(historical)
                    historical_release_baselines.append({"scenario_code": scenario.get("scenario_code"), **historical})

            scenario_gaps = []
            if not flow_junit_evidence:
                scenario_gaps.append("NO_FLOW_JUNIT_EVIDENCE")
            if not current_rows:
                scenario_gaps.append("NO_CURRENT_CHANGE_TEST_BASELINE")
            elif not any(str(t.get("status") or "").upper() == "PASS" for t in current_rows):
                scenario_gaps.append("NO_PASSING_CURRENT_CHANGE_TEST_BASELINE")
            if current_rows and not scenario_current_releases:
                scenario_gaps.append("NO_CURRENT_CHANGE_RELEASE_BASELINE")

            if scenario_gaps:
                gaps.append({
                    "scenario_code": scenario.get("scenario_code"),
                    "gaps": scenario_gaps,
                })

            scenario_lineage.append({
                "scenario_id": scenario_id,
                "scenario_code": scenario.get("scenario_code"),
                "scenario_name": scenario.get("scenario_name"),
                "http_method": scenario.get("http_method"),
                "endpoint": scenario.get("endpoint"),
                "relevance": scenario.get("relevance"),
                "changed_attributes": scenario.get("changed_attributes") or [],
                "flow_junit_evidence": flow_junit_evidence,
                "component_junit_note": (
                    "Component-level JUnit evidence is reported globally and is not scenario coverage."
                    if component_junit_evidence else None
                ),
                "current_change_test_baselines": current_rows,
                "historical_test_baselines": historical_rows,
                "current_change_release_baselines": scenario_current_releases,
                "historical_release_baselines": scenario_historical_releases,
                "gaps": scenario_gaps,
            })

        git_context = self._current_git_context(git_changes.get("git_root"))
        dirty = bool(git_context.get("dirty"))
        if dirty:
            gaps.insert(0, {
                "scope": "CURRENT_GIT",
                "gaps": ["UNCOMMITTED_CHANGES", "CURRENT_CHANGES_NOT_YET_FROZEN_IN_A_COMMIT"],
            })

        if not impacted:
            closure_status = "NO_IMPACTED_SCENARIOS_FOUND"
        elif gaps:
            closure_status = "ACTION_REQUIRED"
        else:
            closure_status = "TRACEABILITY_COMPLETE"

        payload = {
            "closure_status": closure_status,
            "current_git": git_context,
            "changed_attributes": changed_attributes,
            "changed_classes": changed_classes,
            "changed_methods": git_changes.get("changed_methods") or [],
            "confirmed_jiras": sorted(confirmed_jiras),
            "confirmed_requirements": requirement.get("confirmed_requirements") or [],
            "impacted_scenario_count": len(impacted),
            "scenario_lineage": scenario_lineage,
            "regression_recommendation": scenario_impact.get("regression_recommendation") or [],
            "scenario_flow_junit_evidence": all_flow_test_evidence,
            "component_junit_evidence": component_junit_evidence,
            "current_change_test_baselines": current_test_baselines,
            "historical_test_baselines": historical_test_baselines,
            "current_change_release_baselines": current_release_baselines,
            "historical_release_baselines": historical_release_baselines,
            # Backward-compatible names now mean current-change evidence only.
            "static_junit_evidence": all_flow_test_evidence,
            "captured_test_baselines": current_test_baselines,
            "captured_release_baselines": current_release_baselines,
            "traceability_gaps": gaps,
            "next_actions": self._sdlc_next_actions(
                dirty, impacted, all_flow_test_evidence, current_test_baselines, current_release_baselines
            ),
            "evidence_rules": {
                "requirement_jira": "Only confirmed explicit entity evidence from git_requirement_correlation is accepted.",
                "scenario_junit": "Only FLOW_METHOD_MATCH/FLOW_CLASS_MATCH evidence is attached to a scenario.",
                "component_junit": "COMPONENT_STATIC_JUNIT is global changed-component evidence and is never treated as endpoint/scenario coverage.",
                "test_result": "Current-change PASS/FAIL comes only from ScenarioTestBaseline records whose JIRA IDs intersect confirmed current Git JIRAs.",
                "historical_baseline": "Other test/release baselines are retained only as HISTORICAL_CONTEXT and cannot close the current change.",
                "commit": "HEAD identifies the current committed parent; dirty working-tree changes are not claimed as committed.",
                "release": "A release baseline is current-change evidence only when referenced by a current-change test baseline.",
            },
        }
        return json.dumps(payload, default=str)

    @staticmethod
    def _run_git(git_root: str | None, *args: str) -> str:
        if not git_root:
            return ""
        try:
            completed = subprocess.run(
                ["git", "-C", str(git_root), *args],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return (completed.stdout or "").strip() if completed.returncode == 0 else ""
        except Exception:
            return ""

    def _current_git_context(self, git_root: str | None) -> dict[str, Any]:
        root = str(git_root or "").strip()
        branch = self._run_git(root, "branch", "--show-current")
        head = self._run_git(root, "rev-parse", "HEAD")
        short_head = self._run_git(root, "rev-parse", "--short", "HEAD")
        subject = self._run_git(root, "log", "-1", "--pretty=%s")
        status = self._run_git(root, "status", "--porcelain")
        return {
            "git_root": root or None,
            "branch": branch or None,
            "head_commit": head or None,
            "head_commit_short": short_head or None,
            "head_subject": subject or None,
            "dirty": bool(status),
            "working_tree_change_count": len([line for line in status.splitlines() if line.strip()]),
            "meaning": (
                "HEAD is the committed parent of the current working tree; current dirty changes are not yet committed."
                if status else
                "Working tree is clean; HEAD represents the current committed source state."
            ),
        }

    @staticmethod
    def _sdlc_next_actions(dirty: bool, impacted: list, junit: list, tests: list, releases: list) -> list[str]:
        actions = []
        if impacted and not junit:
            actions.append("Add or update JUnit tests that exercise the impacted executable flows.")
        if impacted and not tests:
            actions.append("Execute regression scenarios and capture Test Baselines with PASS/FAIL evidence and JIRA links.")
        elif tests and not any(str(x.get("status") or "").upper() == "PASS" for x in tests):
            actions.append("Resolve failing/not-run Test Baselines and capture passing regression evidence.")
        if impacted and not releases:
            actions.append("Capture the release/code baseline after regression evidence is ready.")
        if dirty:
            actions.append("After tests and baseline capture are complete, commit the current Git changes with the confirmed JIRA/requirement reference.")
        if not actions:
            actions.append("Traceability evidence is complete for the currently detected impacted scenarios.")
        return actions

    def baseline_history(self, attribute: str) -> str:
        """Return release/test-baseline history already linked to an attribute's impacted scenarios."""
        result = AttributeImpactService()._analyze_code_only(str(attribute or "").strip(), self.db)
        history = result.get("historical_traceability") or []
        payload = {
            "attribute": result.get("attribute"),
            "historical_traceability": history,
            "history_count": len(history),
        }
        return json.dumps(payload, default=str)
