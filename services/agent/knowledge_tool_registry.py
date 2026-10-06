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
from services.jira.jira_live_client import jira_live_client
from services.test_analysis.test_code_analysis_service import TestCodeAnalysisService
from baseline_models import ScenarioBaseline, ScenarioTestBaseline, ScenarioReleaseArchive
from db_models import KnowledgeDocument, MappingDocument, MappingDefinition
from services.cross_project.engineering_knowledge_graph_service import EngineeringKnowledgeGraphService
from services.lineage.mapping_intelligence_service import MappingIntelligenceService


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
                func=self.live_jira_issue,
                name="live_jira_issue",
                description=(
                    "Retrieve the authoritative CURRENT issue directly from Live Jira by issue key "
                    "(for example KAN-4). Use whenever the user explicitly says live Jira/current Jira "
                    "or asks to analyze a Jira issue against current source code. This is the source of "
                    "truth for the live requirement text; do not substitute Git, RAG or historical Jira "
                    "documents for this tool."
                ),
            ),
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
                func=self.mapping_lineage,
                name="mapping_lineage",
                description=(
                    "Find authoritative Phase 6 mapping definitions for an attribute/source path. "
                    "Returns source type/path, transformation, Java target, null handling, comments, "
                    "and exact mapping-document provenance including workbook, sheet and row. Use for "
                    "mapping, lineage, XML/JSON/DB source, null/default rule, or 'which mapping document' questions."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_source_lineage,
                name="mapping_source_lineage",
                description=(
                    "Resolve an XML XPath/node, JSON path/node, DB table.column, Kafka field or other source path "
                    "to its authoritative Phase 6 Java target mapping. Returns all matching versions plus exact "
                    "workbook/sheet/row provenance. When include_engineering_impact is true, also joins each resolved "
                    "Java target to the Phase 5 Neo4j engineering impact graph. Use when the user's starting point "
                    "is a source such as /Student/GPA, customer.gpa or STUDENT.TEMP_LOCATION."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_graph_lineage,
                name="mapping_graph_lineage",
                description=(
                    "Traverse Phase 6 MAPS_TO relationships in Neo4j for an attribute and join them to the "
                    "Phase 5 engineering impact graph. Returns mapping source/provenance plus impacted methods, "
                    "endpoints, scenarios, JIRAs, releases and test baselines. Use for mapping + downstream impact questions."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_history,
                name="mapping_history",
                description=(
                    "Show how an attribute mapping evolved across versions in a mapping family. Returns each version, "
                    "field-level changes from the previous version, and exact workbook/sheet/row provenance. "
                    "Use for mapping history/evolution, when/where a mapping changed, or how an attribute changed over time."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_compare,
                name="mapping_compare",
                description=(
                    "Compare two explicit versions of the same mapping family, optionally for one attribute. "
                    "Returns ADDED/REMOVED/CHANGED/UNCHANGED mappings, field-level differences and provenance on both sides. "
                    "Use for questions such as 'compare Student Mapping V1 and V2'."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_validation,
                name="mapping_validation",
                description=(
                    "Validate all Java targets in one Phase 6 mapping document against the current Neo4j code graph. "
                    "Requires a document_id and returns FOUND_IN_CODE_GRAPH / NOT_FOUND_IN_CODE_GRAPH with sheet/row evidence. "
                    "Use when the user asks whether targets introduced by a mapping document/version actually exist in code."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_change_impact,
                name="mapping_change_impact",
                description=(
                    "Return the complete blast radius of a mapping change between two versions: mapping diff, target rename, "
                    "latest code validation, current Neo4j engineering impact, risks, and exact provenance. Use for questions "
                    "such as 'what is the impact of the latest GPA mapping change?'."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_quality,
                name="mapping_quality",
                description=(
                    "Detect mapping quality problems including competing targets, conflicting null rules/transforms, duplicate "
                    "active definitions, and mapping targets missing from current code. Use for conflict/quality/consistency questions."
                ),
            ),
            StructuredTool.from_function(
                func=self.mapping_intelligence,
                name="mapping_intelligence",
                description=(
                    "Return one consolidated Phase 6 mapping-intelligence view for an attribute: authoritative latest/previous "
                    "version resolution, change blast radius, quality/conflicts, validation and provenance. Use for broad questions "
                    "such as 'tell me everything about the latest GPA mapping and its impact'."
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
                func=self.attribute_test_evidence,
                name="attribute_test_evidence",
                description=(
                    "Find test evidence for a business attribute independently of release/test baselines. "
                    "Returns matching static JUnit source evidence plus ingested JUnit TEST_REPORT runtime "
                    "PASS/FAIL evidence for affected scenarios. Use when the user asks which tests cover, "
                    "validate, exercise, or provide evidence for an attribute."
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

    def live_jira_issue(self, issue_key: str) -> str:
        """Retrieve one issue directly from the configured Live Jira instance."""
        issue_key = str(issue_key or "").strip().upper()
        if not issue_key:
            raise ValueError("issue_key is required")
        issue = jira_live_client.get_issue(issue_key)
        return json.dumps(
            {
                "source": "LIVE_JIRA",
                "authoritative": True,
                "issue": issue,
                "evidence_rule": (
                    "This payload is the authoritative live requirement for this analysis. "
                    "Current Git evidence must be evaluated separately and must not be treated "
                    "as implementation evidence for this Jira unless it explicitly matches the requirement."
                ),
            },
            default=str,
        )

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
        """Traverse the Phase 5 engineering graph, then attach document evidence."""
        value = str(entity or "").strip()
        if not value:
            raise ValueError("entity is required")

        engineering_graph = None
        try:
            engineering_graph = EngineeringKnowledgeGraphService().impact(value)
        except Exception as exc:
            engineering_graph = {"status": "UNAVAILABLE", "error": str(exc)}

        direct = self.extractor.extract(value, "REQUIREMENT")
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
        return json.dumps({"entity": entity, "engineering_graph": engineering_graph, "document_graph": graph, "evidence": evidence}, default=str)

    def unified_knowledge_search(self, query: str, top_k: int = 5) -> str:
        """Search all connected knowledge and return consolidated evidence."""
        result = self.unified.search(self.db, query, top_k=max(1, min(int(top_k), 10)))
        return json.dumps(result, default=str)


    def mapping_lineage(self, attribute: str) -> str:
        """Return Phase 6 mapping lineage with exact document/sheet/row evidence."""
        value = str(attribute or "").strip().rstrip(".,?!:;")
        if not value:
            raise ValueError("attribute is required")
        result = MappingIntelligenceService().lineage(self.db, value)
        return json.dumps(result, default=str)

    def mapping_source_lineage(
        self,
        source_path: str,
        include_engineering_impact: bool = False,
        project: str | None = None,
    ) -> str:
        """Resolve a source-side XML/JSON/DB path to mapping targets and optional code impact."""
        value = str(source_path or "").strip().strip('`\"\'').rstrip(".,?!:;")
        if not value:
            raise ValueError("source_path is required")

        service = MappingIntelligenceService()
        service._ensure_mapping_family_column(self.db)
        project_path = service._project_path()
        rows = (
            self.db.query(MappingDefinition, MappingDocument)
            .join(MappingDocument, MappingDocument.id == MappingDefinition.document_id)
            .filter(
                MappingDefinition.project_path == project_path,
                MappingDefinition.status == "ACTIVE",
                MappingDocument.status == "ACTIVE",
            )
            .all()
        )

        def norm(raw: Any) -> str:
            text = str(raw or "").strip().lower()
            # Business users frequently vary harmless whitespace and source prefixes.
            for prefix in ("xpath:", "json:", "jsonpath:", "db:", "table:", "column:"):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            return text

        needle = norm(value)
        matches = []
        targets: list[str] = []
        for row, doc in rows:
            if norm(row.source_path) != needle:
                continue
            target = str(row.target_attribute or "").strip()
            if target and target not in targets:
                targets.append(target)
            matches.append({
                "mapping_id": row.id,
                "mapping_family": str(doc.mapping_family or doc.title or "").strip(),
                "document_version": doc.document_version,
                "source": {
                    "type": row.source_type,
                    "system": row.source_system,
                    "path": row.source_path,
                },
                "target": {
                    "class": row.target_class,
                    "attribute": row.target_attribute,
                    "expression": row.target_expression,
                },
                "rules": {
                    "mapping": row.mapping_rule,
                    "null": row.null_rule,
                    "validation": row.validation_rule,
                    "comments": row.comments,
                },
                "provenance": {
                    "document_id": doc.id,
                    "document": doc.filename,
                    "document_version": doc.document_version,
                    "sheet": row.sheet_name,
                    "row": row.row_number,
                    "source_ref": doc.source_ref,
                },
            })

        # Sort versions naturally while preserving all historical evidence.
        matches.sort(key=lambda item: service._version_key(item.get("document_version")))
        impacts = []
        if include_engineering_impact:
            requested_project = str(project or "").strip() or None
            for target in targets:
                try:
                    impact = EngineeringKnowledgeGraphService().impact(target, requested_project)
                except Exception as exc:
                    impact = {"status": "UNAVAILABLE", "entity": target, "error": str(exc)}
                impacts.append({"target_attribute": target, "engineering_impact": impact})

        return json.dumps({
            "status": "FOUND" if matches else "NO_MATCH",
            "source_path": value,
            "mappings": matches,
            "mapping_count": len(matches),
            "resolved_target_attributes": targets,
            "engineering_impacts": impacts,
            "evidence_rule": "Every source resolution preserves mapping document, version, sheet and row provenance.",
        }, default=str)

    def mapping_graph_lineage(self, attribute: str, project: str | None = None) -> str:
        """Join Phase 6 MAPS_TO evidence to the Phase 5 Neo4j engineering-impact graph."""
        value = str(attribute or "").strip().rstrip(".,?!:;")
        if not value:
            raise ValueError("attribute is required")
        project_value = str(project or "").strip() or None
        result = MappingIntelligenceService().graph_lineage(value, project_value)
        return json.dumps(result, default=str)

    def mapping_history(self, attribute: str, mapping_family: str | None = None) -> str:
        """Return version-by-version mapping evolution with exact provenance."""
        value = str(attribute or "").strip().rstrip(".,?!:;")
        if not value:
            raise ValueError("attribute is required")
        family = str(mapping_family or "").strip() or None
        result = MappingIntelligenceService().mapping_history(self.db, value, family)
        return json.dumps(result, default=str)

    def mapping_compare(
        self,
        mapping_family: str,
        from_version: str,
        to_version: str,
        attribute: str | None = None,
    ) -> str:
        """Compare two versions within one mapping family."""
        family = str(mapping_family or "").strip()
        old_version = str(from_version or "").strip()
        new_version = str(to_version or "").strip()
        attr = str(attribute or "").strip().rstrip(".,?!:;") or None
        if not old_version or not new_version:
            raise ValueError("from_version and to_version are required")

        # Agent-safe resolution: the LLM may send a descriptive family such as
        # "GPA mapping" even though the authoritative family is "Student Mapping".
        # Resolve the unique family that actually contains both requested versions
        # and (when supplied) the requested attribute before calling Phase 6G.
        context = self.resolve_mapping_context(
            attribute=attr,
            from_version=old_version,
            to_version=new_version,
            mapping_family=family or None,
        )
        resolved_family = context.get("mapping_family")
        if not resolved_family:
            raise ValueError("requested mapping family/version was not found")

        result = MappingIntelligenceService().compare_versions(
            self.db, resolved_family, old_version, new_version, attr
        )
        result["requested_mapping_family"] = family or None
        result["resolved_mapping_family"] = resolved_family
        return json.dumps(result, default=str)


    def resolve_mapping_context(
        self,
        attribute: str | None = None,
        from_version: str | None = None,
        to_version: str | None = None,
        mapping_family: str | None = None,
    ) -> dict[str, Any]:
        """Resolve family/version/document IDs from authoritative mapping metadata.

        This is intentionally deterministic and is used by the agent adapter so a
        natural-language phrase such as "GPA mapping V2 to V3" does not have to
        exactly spell the stored mapping-family name.
        """
        service = MappingIntelligenceService()
        service._ensure_mapping_family_column(self.db)
        project_path = service._project_path()
        docs = self.db.query(MappingDocument).filter(
            MappingDocument.project_path == project_path,
            MappingDocument.status == "ACTIVE",
        ).all()

        wanted_versions = {
            str(v).strip().lower() for v in (from_version, to_version) if str(v or "").strip()
        }
        requested_family = str(mapping_family or "").strip()
        attribute_value = str(attribute or "").strip().lower()

        grouped: dict[str, list[MappingDocument]] = {}
        for doc in docs:
            family = str(doc.mapping_family or doc.title or "").strip()
            if family:
                grouped.setdefault(family, []).append(doc)

        candidates: list[tuple[str, list[MappingDocument]]] = []
        for family, family_docs in grouped.items():
            versions = {str(d.document_version or "").strip().lower() for d in family_docs}
            if wanted_versions and not wanted_versions.issubset(versions):
                continue
            if attribute_value:
                doc_ids = [d.id for d in family_docs]
                rows = self.db.query(MappingDefinition).filter(
                    MappingDefinition.document_id.in_(doc_ids),
                    MappingDefinition.status == "ACTIVE",
                ).all()
                if not any(
                    attribute_value in str(r.target_attribute or "").lower()
                    or attribute_value in str(r.target_expression or "").lower()
                    for r in rows
                ):
                    continue
            candidates.append((family, family_docs))

        # Prefer an exact authoritative family name when it is valid. Otherwise,
        # resolve only when the metadata leaves one unambiguous candidate.
        exact = next((x for x in candidates if requested_family and x[0].lower() == requested_family.lower()), None)
        selected = exact or (candidates[0] if len(candidates) == 1 else None)
        if not selected:
            return {
                "mapping_family": None,
                "candidates": [name for name, _ in candidates],
                "reason": "AMBIGUOUS" if len(candidates) > 1 else "NOT_FOUND",
            }

        family, family_docs = selected
        by_version = {str(d.document_version or "").strip().lower(): d for d in family_docs}
        return {
            "mapping_family": family,
            "from_document_id": getattr(by_version.get(str(from_version or "").strip().lower()), "id", None),
            "to_document_id": getattr(by_version.get(str(to_version or "").strip().lower()), "id", None),
            "candidates": [name for name, _ in candidates],
            "reason": "RESOLVED",
        }


    def resolve_relative_mapping_context(
        self,
        attribute: str | None = None,
        relative_version: str = "latest",
        mapping_family: str | None = None,
    ) -> dict[str, Any]:
        """Resolve latest/previous mapping document without exposing document-id choice to the LLM.

        Attribute matching is performed across the whole family, then the requested
        relative document is selected from that family's versions. This is important
        for renamed targets: an older GPA row can establish the Student Mapping family
        even when the latest document renamed the target.
        """
        service = MappingIntelligenceService()
        service._ensure_mapping_family_column(self.db)
        project_path = service._project_path()
        docs = self.db.query(MappingDocument).filter(
            MappingDocument.project_path == project_path,
            MappingDocument.status == "ACTIVE",
        ).all()

        requested_family = str(mapping_family or "").strip()
        attribute_value = str(attribute or "").strip().lower()
        relative = str(relative_version or "latest").strip().lower()
        if relative not in {"latest", "current", "newest", "previous", "prior"}:
            raise ValueError("relative_version must be latest/current/newest/previous/prior")

        grouped: dict[str, list[MappingDocument]] = {}
        for doc in docs:
            family = str(doc.mapping_family or doc.title or "").strip()
            if family:
                grouped.setdefault(family, []).append(doc)

        candidates: list[tuple[str, list[MappingDocument]]] = []
        for family, family_docs in grouped.items():
            if requested_family and family.lower() != requested_family.lower():
                continue
            if attribute_value:
                doc_ids = [d.id for d in family_docs]
                rows = self.db.query(MappingDefinition).filter(
                    MappingDefinition.document_id.in_(doc_ids),
                    MappingDefinition.status == "ACTIVE",
                ).all()
                if not any(
                    attribute_value == str(row.target_attribute or "").strip().lower()
                    or attribute_value == str(row.target_expression or "").strip().lower().split(".")[-1]
                    or attribute_value == str(row.source_path or "").strip().lower().split("/")[-1].split(".")[-1]
                    for row in rows
                ):
                    continue
            candidates.append((family, family_docs))

        # If the caller supplied an exact family, the filter above already makes
        # the choice authoritative. For attribute-only relative questions we may
        # legitimately find more than one family (for example a one-off conflict
        # test plus the real versioned Student Mapping family). Never fall back to
        # an LLM-guessed document id. Prefer the uniquely strongest version lineage:
        # the family with the greatest number of active mapping documents.
        if not candidates:
            return {
                "mapping_family": None,
                "document_version": None,
                "document_id": None,
                "candidates": [],
                "reason": "NOT_FOUND",
            }

        if len(candidates) > 1:
            ranked = sorted(
                candidates,
                key=lambda item: (
                    len(item[1]),
                    max(
                        (service._version_key(d.document_version) for d in item[1]),
                        default=(),
                    ),
                ),
                reverse=True,
            )
            top_count = len(ranked[0][1])
            equally_versioned = [item for item in ranked if len(item[1]) == top_count]

            # Resolve only when one family has a strictly stronger version lineage.
            # If two real families are equally strong, preserve ambiguity rather
            # than guessing.
            if len(equally_versioned) != 1:
                return {
                    "mapping_family": None,
                    "document_version": None,
                    "document_id": None,
                    "candidates": [name for name, _ in ranked],
                    "reason": "AMBIGUOUS",
                }
            candidates = [equally_versioned[0]]

        family, family_docs = candidates[0]
        ordered = sorted(
            family_docs,
            key=lambda d: (service._version_key(d.document_version), int(d.id or 0)),
        )
        use_previous = relative in {"previous", "prior"}
        if use_previous and len(ordered) < 2:
            return {
                "mapping_family": family,
                "document_version": None,
                "document_id": None,
                "candidates": [family],
                "reason": "NO_PREVIOUS_VERSION",
            }
        selected = ordered[-2] if use_previous else ordered[-1]
        return {
            "mapping_family": family,
            "document_version": selected.document_version,
            "document_id": selected.id,
            "candidates": [family],
            "reason": "RESOLVED",
        }

    def mapping_change_impact(
        self,
        mapping_family: str,
        from_version: str,
        to_version: str,
        attribute: str | None = None,
        project: str | None = None,
    ) -> str:
        """Return mapping diff + code validation + Neo4j engineering blast radius."""
        family = str(mapping_family or "").strip()
        old = str(from_version or "").strip()
        new = str(to_version or "").strip()
        attr = str(attribute or "").strip().rstrip(".,?!:;") or None
        if not family or not old or not new:
            raise ValueError("mapping_family, from_version and to_version are required")
        result = MappingIntelligenceService().change_impact(self.db, family, old, new, attr, project)
        return json.dumps(result, default=str)

    def mapping_quality(
        self,
        attribute: str | None = None,
        source_path: str | None = None,
        mapping_family: str | None = None,
    ) -> str:
        """Return authoritative mapping conflicts/quality findings."""
        result = MappingIntelligenceService().quality(
            self.db,
            str(attribute or "").strip() or None,
            str(source_path or "").strip() or None,
            str(mapping_family or "").strip() or None,
        )
        return json.dumps(result, default=str)

    def mapping_intelligence(self, attribute: str, relative_version: str = "latest", project: str | None = None) -> str:
        """Consolidate latest/previous mapping change, validation, impact and quality."""
        attr = str(attribute or "").strip().rstrip(".,?!:;")
        if not attr:
            raise ValueError("attribute is required")
        relative = str(relative_version or "latest").strip().lower()
        context = self.resolve_relative_mapping_context(attribute=attr, relative_version=relative)
        if not context.get("document_id") or not context.get("mapping_family"):
            return json.dumps({"status": "UNRESOLVED", "attribute": attr, "resolution": context}, default=str)

        service = MappingIntelligenceService()
        docs = service._family_documents(self.db, context["mapping_family"])
        ordered = sorted(docs, key=lambda d: (service._version_key(d.document_version), int(d.id or 0)))
        selected_index = next((i for i, d in enumerate(ordered) if d.id == context["document_id"]), -1)
        selected = ordered[selected_index] if selected_index >= 0 else None
        previous = ordered[selected_index - 1] if selected_index > 0 else None

        validation = service.validate(self.db, context["document_id"])
        quality = service.quality(self.db, attribute=attr)
        impact = None
        if selected is not None and previous is not None:
            impact = service.change_impact(
                self.db,
                context["mapping_family"],
                str(previous.document_version or ""),
                str(selected.document_version or ""),
                attr,
                project,
            )
        return json.dumps({
            "status": "FOUND",
            "attribute": attr,
            "resolution": context,
            "previous_version": previous.document_version if previous else None,
            "validation": validation,
            "change_impact": impact,
            "quality": quality,
            "evidence_rule": "This response is assembled deterministically from mapping metadata, provenance and the current Neo4j engineering graph.",
        }, default=str)

    def mapping_validation(self, document_id: int) -> str:
        """Validate one mapping document's Java targets against the current code graph."""
        if document_id is None:
            raise ValueError("document_id is required")
        result = MappingIntelligenceService().validate(self.db, int(document_id))
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

    @staticmethod
    def _normalise_junit_class(value: str | None) -> str:
        """Return a comparable Java test-class name (FQCN and simple names match)."""
        text = str(value or "").strip()
        return text.rsplit(".", 1)[-1].strip().lower()

    @staticmethod
    def _normalise_junit_method(value: str | None) -> str:
        """Normalise Gradle/Surefire testcase names to the source-method name.

        Gradle commonly writes `methodName()` while static source analysis returns
        `methodName`. Parameterized reports can also append `(args)` or `[index]`.
        """
        text = str(value or "").strip()
        if not text:
            return ""
        # Remove a trailing invocation/parameter display: foo(), foo(String), etc.
        if "(" in text:
            text = text.split("(", 1)[0].strip()
        # JUnit parameterized display names may be foo[1].
        if "[" in text:
            text = text.split("[", 1)[0].strip()
        return text.lower()

    def _runtime_junit_evidence(self, static_evidence: list[dict]) -> list[dict]:
        """Correlate static JUnit evidence with ingested runtime TEST_REPORTs.

        Matching is deliberately evidence-only, but tolerant of the naming differences
        between Java source (`shouldWork`) and Gradle/Surefire XML (`shouldWork()`).
        Newest matching report wins for each static class+method pair.
        """
        if not static_evidence:
            return []

        project_path = str(getattr(__import__("config").settings, "JAVA_PROJECT_PATH", "") or "").strip()
        query = (
            self.db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.source_type == "TEST_REPORT")
            .filter(KnowledgeDocument.status == "ACTIVE")
        )
        if project_path:
            # Windows paths are case-insensitive in practice; avoid losing valid evidence
            # because config/report casing or a trailing slash differs.
            normal_project = project_path.rstrip("\\/").lower()
            reports = query.order_by(
                KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()
            ).all()
            reports = [
                r for r in reports
                if str(r.project_path or "").rstrip("\\/").lower() == normal_project
            ]
        else:
            reports = query.order_by(
                KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()
            ).all()

        wanted: dict[tuple[str, str], dict] = {}
        for evidence in static_evidence:
            method = self._normalise_junit_method(evidence.get("test_method"))
            if not method:
                continue
            clazz = self._normalise_junit_class(evidence.get("test_class"))
            wanted[(clazz, method)] = evidence

        found: dict[tuple[str, str], dict] = {}
        for report in reports:
            content = str(report.content or "")
            current_test = current_class = current_status = None

            def flush() -> None:
                if not current_test or not current_status:
                    return
                report_method = self._normalise_junit_method(current_test)
                report_class = self._normalise_junit_class(current_class)
                candidates = [
                    key for key in wanted
                    if key[1] == report_method
                    and (not report_class or not key[0] or key[0] == report_class)
                ]
                for key in candidates:
                    if key in found:
                        continue  # reports are newest-first
                    static = wanted[key]
                    found[key] = {
                        "test_class": str(static.get("test_class") or current_class or ""),
                        "test_method": str(static.get("test_method") or current_test or ""),
                        "status": str(current_status).upper(),
                        "source_type": "TEST_REPORT",
                        "report_id": report.id,
                        "report_title": report.title,
                        "source_ref": report.source_ref,
                        "coverage_classification": static.get("coverage_classification"),
                        "evidence_scope": "SCENARIO_RUNTIME_JUNIT",
                    }

            for raw_line in content.splitlines():
                line = raw_line.strip()
                if line.startswith("Test: "):
                    flush()
                    current_test = line[6:].strip()
                    current_class = None
                    current_status = None
                elif line.startswith("Class: "):
                    current_class = line[7:].strip()
                elif line.startswith("Qualified Test Class: "):
                    current_class = line[len("Qualified Test Class: "):].strip()
                elif line.startswith("Qualified Test Class "):
                    # Backward compatible with reports ingested before the colon fix.
                    current_class = line[len("Qualified Test Class "):].strip()
                elif line.startswith("Result: "):
                    current_status = line[8:].strip().upper()
            flush()

        return list(found.values())

    def git_sdlc_traceability(self, jira_id: str | None = None) -> str:
        """Build evidence-only SDLC lineage for the current Git working tree.

        When jira_id is supplied, captured test/release baselines are classified as
        CURRENT_JIRA only when the test baseline explicitly contains that Jira ID.
        Older baselines for the same scenario remain available as historical evidence
        but never satisfy current-Jira baseline readiness.
        """
        requirement = json.loads(self.git_requirement_correlation())
        scenario_impact = json.loads(self.git_scenario_impact())
        git_changes = requirement.get("git_changes") or {}
        impacted = scenario_impact.get("impacted_scenarios") or []

        test_scanner = TestCodeAnalysisService()
        test_analysis = test_scanner.analyse()
        scenario_lineage = []
        all_test_evidence = []
        all_test_baselines = []
        all_releases = []
        gaps = []
        requested_jira = str(jira_id or "").strip().upper() or None

        for scenario in impacted:
            scenario_id = scenario.get("id")
            if scenario_id is None:
                continue

            junit_evidence = test_scanner.evidence_for_scenario(scenario, test_analysis)
            runtime_junit_evidence = self._runtime_junit_evidence(junit_evidence)
            for evidence in junit_evidence:
                tagged = dict(evidence)
                tagged["scenario_id"] = scenario_id
                tagged["scenario_code"] = scenario.get("scenario_code")
                all_test_evidence.append(tagged)

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
                all_releases.append({"scenario_code": scenario.get("scenario_code"), **release})

            test_rows = []
            current_jira_test_rows = []
            historical_test_rows = []
            current_jira_baseline_ids = set()
            for test in test_baselines:
                jira_ids = [str(x).strip().upper() for x in (test.jira_ids or []) if str(x).strip()]
                matches_requested_jira = bool(requested_jira and requested_jira in jira_ids)
                row = {
                    "test_baseline_id": test.id,
                    "name": test.baseline_name,
                    "status": test.status,
                    "jira_ids": test.jira_ids or [],
                    "baseline_id": test.baseline_id,
                    "code_baseline_version": test.code_baseline_version,
                    "created_at": test.created_at,
                    "evidence_scope": (
                        "CURRENT_JIRA_BASELINE" if matches_requested_jira
                        else "HISTORICAL_SCENARIO_BASELINE" if requested_jira
                        else "SCENARIO_BASELINE"
                    ),
                }
                test_rows.append(row)
                all_test_baselines.append({"scenario_code": scenario.get("scenario_code"), **row})
                if matches_requested_jira:
                    current_jira_test_rows.append(row)
                    if test.baseline_id is not None:
                        current_jira_baseline_ids.add(test.baseline_id)
                elif requested_jira:
                    historical_test_rows.append(row)

            # A release baseline belongs to the requested Jira only through an
            # explicitly Jira-linked test baseline. Scenario reuse alone is not enough.
            current_jira_release_rows = [
                row for row in release_rows if row.get("baseline_id") in current_jira_baseline_ids
            ] if requested_jira else release_rows

            scenario_gaps = []
            if not junit_evidence:
                scenario_gaps.append("NO_STATIC_JUNIT_FLOW_EVIDENCE")
            effective_releases = current_jira_release_rows if requested_jira else release_rows
            effective_tests = current_jira_test_rows if requested_jira else test_rows
            if not effective_releases:
                scenario_gaps.append("NO_RELEASE_BASELINE_FOR_CURRENT_JIRA" if requested_jira else "NO_RELEASE_BASELINE")
            if not effective_tests:
                scenario_gaps.append("NO_TEST_BASELINE_FOR_CURRENT_JIRA" if requested_jira else "NO_TEST_BASELINE")
            elif not any(str(t.get("status") or "").upper() == "PASS" for t in effective_tests):
                scenario_gaps.append("NO_PASSING_TEST_BASELINE_FOR_CURRENT_JIRA" if requested_jira else "NO_PASSING_TEST_BASELINE")

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
                "junit_evidence": junit_evidence,
                "runtime_junit_evidence": runtime_junit_evidence,
                "runtime_test_status": (
                    "FAIL" if any(x.get("status") == "FAIL" for x in runtime_junit_evidence)
                    else "PASS" if runtime_junit_evidence and all(x.get("status") == "PASS" for x in runtime_junit_evidence)
                    else "PARTIAL" if runtime_junit_evidence
                    else "NOT_INGESTED"
                ),
                "release_baselines": current_jira_release_rows if requested_jira else release_rows,
                "test_baselines": current_jira_test_rows if requested_jira else test_rows,
                "historical_release_baselines": release_rows if requested_jira else [],
                "historical_test_baselines": historical_test_rows if requested_jira else [],
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
            "requested_jira": requested_jira,
            "current_git": git_context,
            "changed_attributes": git_changes.get("changed_attributes") or [],
            "changed_classes": git_changes.get("changed_classes") or [],
            "changed_methods": git_changes.get("changed_methods") or [],
            "confirmed_jiras": requirement.get("confirmed_jiras") or [],
            "confirmed_requirements": requirement.get("confirmed_requirements") or [],
            "impacted_scenario_count": len(impacted),
            "scenario_lineage": scenario_lineage,
            "regression_recommendation": scenario_impact.get("regression_recommendation") or [],
            "static_junit_evidence": all_test_evidence,
            "captured_test_baselines": all_test_baselines,
            "captured_release_baselines": all_releases,
            "traceability_gaps": gaps,
            "next_actions": self._sdlc_next_actions(dirty, impacted, all_test_evidence, all_test_baselines, all_releases),
            "evidence_rules": {
                "requirement_jira": "Only confirmed explicit entity evidence from git_requirement_correlation is accepted.",
                "junit": "Static Java test-source evidence only; not runtime/JaCoCo coverage.",
                "test_result": "JUnit runtime PASS/FAIL is reported only from ingested TEST_REPORT evidence; baseline PASS/FAIL remains sourced from ScenarioTestBaseline records.",
                "commit": "HEAD identifies the current committed parent; dirty working-tree changes are not claimed as committed.",
                "release": "Release lineage is reported only from captured ScenarioBaseline/ReleaseArchive records.",
                "current_jira_baseline": "When requested_jira is present, only test baselines explicitly linked to that Jira (and their parent release baseline) count as current-Jira baseline evidence. Other scenario baselines are historical only.",
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

    def attribute_test_evidence(self, attribute: str) -> str:
        """Return static + runtime JUnit evidence for an attribute, independent of baselines."""
        attribute = str(attribute or "").strip()
        if not attribute:
            raise ValueError("attribute is required")

        impact = AttributeImpactService()._analyze_code_only(attribute, self.db)
        scenarios = impact.get("affected_scenarios") or []
        scanner = TestCodeAnalysisService()
        analysis = scanner.analyse()

        static_rows = []
        runtime_rows = []
        seen_static = set()
        seen_runtime = set()

        for scenario in scenarios:
            scenario_payload = dict(scenario)
            # Attribute-driven evidence must require the requested attribute.
            scenario_payload["changed_attributes"] = [attribute]
            static = scanner.evidence_for_scenario(scenario_payload, analysis)
            runtime = self._runtime_junit_evidence(static)

            for row in static:
                key = (row.get("test_class"), row.get("test_method"), scenario.get("scenario_code"))
                if key in seen_static:
                    continue
                seen_static.add(key)
                static_rows.append({
                    **row,
                    "scenario_id": scenario.get("id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "http_method": scenario.get("http_method"),
                    "endpoint": scenario.get("endpoint"),
                    "scenario_relevance": scenario.get("relevance"),
                })

            for row in runtime:
                key = (row.get("test_class"), row.get("test_method"), scenario.get("scenario_code"), row.get("report_id"))
                if key in seen_runtime:
                    continue
                seen_runtime.add(key)
                runtime_rows.append({
                    **row,
                    "scenario_id": scenario.get("id"),
                    "scenario_code": scenario.get("scenario_code"),
                    "http_method": scenario.get("http_method"),
                    "endpoint": scenario.get("endpoint"),
                    "scenario_relevance": scenario.get("relevance"),
                })

        statuses = {str(x.get("status") or "").upper() for x in runtime_rows}
        runtime_status = (
            "FAIL" if "FAIL" in statuses
            else "PASS" if runtime_rows and statuses <= {"PASS"}
            else "PARTIAL" if runtime_rows
            else "NOT_INGESTED"
        )
        return json.dumps({
            "attribute": attribute,
            "static_junit_evidence": static_rows,
            "runtime_junit_evidence": runtime_rows,
            "runtime_test_status": runtime_status,
            "static_test_count": len({(x.get("test_class"), x.get("test_method")) for x in static_rows}),
            "runtime_test_count": len({(x.get("test_class"), x.get("test_method")) for x in runtime_rows}),
            "evidence_rule": "Runtime PASS/FAIL is sourced only from ingested TEST_REPORT documents; baseline history is separate evidence.",
        }, default=str)

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
