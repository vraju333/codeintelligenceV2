from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from sqlalchemy.orm import Session

from config import settings
from services.flow.endpoint_flow_service import EndpointFlowService
from services.rag.rag_service import RagService
from services.scanner.java_scanner_service import JavaScannerService
from services.scenario.scenario_service import ScenarioService
from services.jira.requirement_llm_service import RequirementLlmService


class JiraImpactService:
    """Project-driven Jira/requirement impact analysis.

    The service intentionally avoids domain-specific rules such as Employee,
    Student, Address, etc.  The selected Java project is the source of truth.
    Requirement text only supplies concepts; RAG + source structure provide the
    evidence used to rank files, endpoints and scenarios.
    """

    STOP_WORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "into", "is", "it", "of", "on", "or", "our",
        "should", "that", "the", "their", "this", "to", "we", "when", "while",
        "with", "will", "can", "must", "need", "needs", "new", "existing",
        "return", "returned", "response", "request", "store", "stored",
        "persist", "persisted", "create", "creating", "update", "updated",
        "delete", "remove", "add", "support", "allow", "include", "expose",
    }

    INTENT_GROUPS = {
        "ADD": {"add", "introduce", "create", "capture", "support", "include", "expose", "allow"},
        "UPDATE": {"update", "change", "modify", "replace", "rename", "enhance"},
        "REMOVE": {"remove", "delete", "drop", "deprecate"},
        "READ": {"return", "display", "show", "fetch", "retrieve", "response", "expose"},
        "PERSIST": {"store", "persist", "save", "database", "db"},
        "VALIDATE": {"validate", "validation", "mandatory", "required", "reject", "verify"},
    }

    TYPE_SUFFIXES = (
        "Request", "Response", "Dto", "DTO", "Entity", "Model", "Mapper",
        "Service", "ServiceImpl", "Controller", "Repository", "Repo"
    )

    def __init__(self, rag_service: RagService | None = None):
        self.scanner = JavaScannerService()
        self.endpoint_flow = EndpointFlowService()
        # Reuse the application's already-loaded embedding/index service when supplied.
        # Creating HuggingFace embeddings for every Jira click would be unnecessarily expensive.
        self.rag = rag_service or RagService()
        self.scenarios = ScenarioService()
        self.requirement_llm = RequirementLlmService()

    def analyze(self, jira_id: str | None, requirement: str, db: Session) -> dict:
        requirement = (requirement or "").strip()
        if not requirement:
            raise ValueError("Requirement / description is required")

        scan = self.scanner.scan()
        project_root = Path(scan.project_path)
        classes = scan.classes
        endpoints = self.endpoint_flow.discover_endpoints()

        requirement_tokens = self._tokenize(requirement)
        code_terms = self._extract_code_terms(requirement)

        llm_used = False
        llm_error = None
        if settings.JIRA_LLM_ENABLED and self.requirement_llm.is_configured():
            try:
                understanding = self.requirement_llm.parse(requirement)
                llm_used = True
            except Exception as exc:
                llm_error = str(exc)
                understanding = None
        else:
            understanding = None

        if understanding:
            intents = understanding.get("intents") or [understanding.get("action") or "CHANGE"]
            requirement_structure = {
                "action": understanding.get("action") or "CHANGE",
                "attribute": understanding.get("attribute"),
                "parent": understanding.get("parent"),
                "entity": understanding.get("entity"),
                "condition": understanding.get("condition"),
                "desired_behavior": understanding.get("desired_behavior"),
                "attributes": understanding.get("attributes") or [],
            }
            concepts = understanding.get("concepts") or self._extract_concepts(
                requirement, requirement_tokens, code_terms, requirement_structure
            )
        else:
            intents = self._detect_intents(requirement)
            requirement_structure = self._parse_requirement_structure(requirement, intents)
            concepts = self._extract_concepts(
                requirement,
                requirement_tokens,
                code_terms,
                requirement_structure,
            )

        requirement_structure = self._normalize_requirement_java_names(requirement_structure)

        project_match = self._assess_project_match(classes, requirement_structure, concepts)
        if project_match["status"] == "MISMATCH":
            return {
                "status": "PROJECT_MISMATCH",
                "jira_id": (jira_id or "").strip() or None,
                "project_path": scan.project_path,
                "requirement": requirement,
                "requirement_understanding": {
                    "action": requirement_structure.get("action"),
                    "attribute": requirement_structure.get("attribute"),
                    "parent": requirement_structure.get("parent"),
                    "entity": requirement_structure.get("entity"),
                    "attributes": requirement_structure.get("attributes") or [],
                    "condition": requirement_structure.get("condition"),
                    "desired_behavior": requirement_structure.get("desired_behavior"),
                    "concepts": concepts,
                    "code_terms": code_terms,
                    "intents": intents,
                    "note": "Requirement was understood, but the selected Java project does not contain strong evidence for the requested domain entity."
                },
                "project_match": project_match,
                "warnings": [project_match["message"]],
                "likely_files": [],
                "affected_endpoints": [],
                "affected_scenarios": [],
                "dependency_paths": [],
                "confidence": {
                    "score": 0,
                    "level": "LOW",
                    "note": "Select the Java project that contains the requested domain before running impact analysis."
                },
                "analysis_basis": {
                    "java_classes_scanned": len(classes),
                    "endpoints_scanned": len(endpoints),
                    "rag_hits_used": 0,
                    "project_driven": True,
                    "llm_used": llm_used,
                    "llm_provider": self.requirement_llm.provider() if llm_used else None,
                    "llm_scope": "requirement_text_only" if llm_used else "not_used",
                    "source_code_sent_to_llm": False,
                    "llm_error": llm_error,
                },
            }

        rag_hits = self._rag_search(requirement, concepts)
        file_evidence = self._score_files(
            project_root=project_root,
            classes=classes,
            requirement=requirement,
            requirement_tokens=requirement_tokens,
            concepts=concepts,
            rag_hits=rag_hits,
        )

        ranked_files = sorted(
            file_evidence.values(),
            key=lambda item: (-item["score"], item["file_name"].lower()),
        )

        # Keep meaningful evidence only, but always show a small RAG-backed set.
        likely_files = [item for item in ranked_files if item["score"] >= 3][:12]
        if not likely_files:
            likely_files = ranked_files[:8]

        candidate_classes = {
            item["class_name"] for item in likely_files if item.get("class_name")
        }

        affected_endpoints = self._find_affected_endpoints(
            endpoints=endpoints,
            candidate_classes=candidate_classes,
            requirement_tokens=requirement_tokens,
            concepts=concepts,
            intents=intents,
            requirement_structure=requirement_structure,
        )

        active_scenarios = self.scenarios.get_all_for_active_project(db)
        affected_scenarios = self._find_affected_scenarios(
            active_scenarios,
            affected_endpoints,
            candidate_classes,
            concepts,
            requirement_structure,
        )

        dependency_paths = self._build_dependency_paths(
            affected_endpoints,
            candidate_classes,
            concepts,
        )

        confidence = self._confidence(likely_files, affected_endpoints, affected_scenarios)

        return {
            "status": "ANALYSIS_COMPLETE",
            "jira_id": (jira_id or "").strip() or None,
            "project_path": scan.project_path,
            "requirement": requirement,
            "requirement_understanding": {
                "action": requirement_structure.get("action"),
                "attribute": requirement_structure.get("attribute"),
                "parent": requirement_structure.get("parent"),
                "entity": requirement_structure.get("entity"),
                "attributes": requirement_structure.get("attributes") or [],
                "condition": requirement_structure.get("condition"),
                "desired_behavior": requirement_structure.get("desired_behavior"),
                "concepts": concepts,
                "code_terms": code_terms,
                "intents": intents,
                "note": "LLM understands requirement prose only; Java code evidence comes from the selected project." if llm_used else "Local fallback parser used; Java code evidence comes from the selected project."
            },
            "project_match": project_match,
            "warnings": [],
            "likely_files": likely_files,
            "affected_endpoints": affected_endpoints,
            "affected_scenarios": affected_scenarios,
            "dependency_paths": dependency_paths,
            "confidence": confidence,
            "analysis_basis": {
                "java_classes_scanned": len(classes),
                "endpoints_scanned": len(endpoints),
                "rag_hits_used": len(rag_hits),
                "project_driven": True,
                "llm_used": llm_used,
                "llm_provider": self.requirement_llm.provider() if llm_used else None,
                "llm_scope": "requirement_text_only" if llm_used else "not_used",
                "source_code_sent_to_llm": False,
                "llm_error": llm_error,
            },
        }

    def _normalize_requirement_java_names(self, structure: dict) -> dict:
        result = dict(structure or {})
        attribute = result.get("attribute")
        if attribute:
            result["attribute"] = self._to_java_field_name(attribute)

        attributes = []
        for value in result.get("attributes") or []:
            normalized = self._to_java_field_name(value)
            if normalized and normalized not in attributes:
                attributes.append(normalized)
        if result.get("attribute") and result["attribute"] not in attributes:
            attributes.insert(0, result["attribute"])
        result["attributes"] = attributes
        return result

    @staticmethod
    def _to_java_field_name(value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        # Preserve an already-valid lower camelCase Java identifier.
        if re.fullmatch(r"[a-z_$][A-Za-z0-9_$]*", text) and " " not in text and "-" not in text:
            return text

        words = re.findall(r"[A-Za-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text.replace("_", " ").replace("-", " ")))
        if not words:
            return text
        first = words[0][:1].lower() + words[0][1:]
        return first + "".join(word[:1].upper() + word[1:] for word in words[1:])

    def _assess_project_match(self, classes: Iterable, requirement_structure: dict, concepts: list[str]) -> dict:
        entity = (requirement_structure.get("entity") or "").strip()
        if not entity:
            return {"status": "UNKNOWN", "score": 0, "message": "No explicit domain entity was identified in the requirement."}

        entity_tokens = {p.lower() for p in self._split_identifier(entity)}
        if not entity_tokens:
            return {"status": "UNKNOWN", "score": 0, "message": "No explicit domain entity was identified in the requirement."}

        strong_matches = []
        weak_matches = []
        for cls in classes:
            class_tokens = {p.lower() for p in self._split_identifier(cls.class_name)}
            if entity_tokens.issubset(class_tokens):
                strong_matches.append(cls.class_name)
            elif entity_tokens.intersection(class_tokens):
                weak_matches.append(cls.class_name)

        if strong_matches:
            return {
                "status": "MATCH",
                "score": 100,
                "entity": entity,
                "matched_classes": strong_matches[:8],
                "message": f"Selected project contains strong matches for requirement entity '{entity}'.",
            }

        if weak_matches:
            return {
                "status": "POSSIBLE",
                "score": 45,
                "entity": entity,
                "matched_classes": weak_matches[:8],
                "message": f"Selected project has only partial matches for requirement entity '{entity}'. Review the selected project.",
            }

        return {
            "status": "MISMATCH",
            "score": 0,
            "entity": entity,
            "matched_classes": [],
            "message": f"Requirement appears to target '{entity}', but the selected project has no matching domain class. Select the correct Java project before impact analysis.",
        }

    def _tokenize(self, text: str) -> set[str]:
        tokens: set[str] = set()
        for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
            for part in self._split_identifier(raw):
                value = part.lower()
                if len(value) >= 3 and value not in self.STOP_WORDS:
                    tokens.add(value)
        return tokens

    def _split_identifier(self, value: str) -> list[str]:
        value = value.replace("_", " ")
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
        return re.findall(r"[A-Za-z0-9]+", value)

    def _extract_code_terms(self, requirement: str) -> list[str]:
        terms: list[str] = []

        # Explicit code-looking identifiers are strong hints.
        for value in re.findall(r"[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]", requirement):
            terms.append(value)
        for value in re.findall(r"\b[A-Za-z]+[A-Z][A-Za-z0-9_]*\b", requirement):
            terms.append(value)

        # Generic grammar: "attribute X", "field X", "property X".
        pattern = re.compile(
            r"\b(?:attribute|field|property|column|parameter|value)\s+(?:named\s+)?([A-Za-z_][A-Za-z0-9_]*)",
            re.IGNORECASE,
        )
        terms.extend(match.group(1) for match in pattern.finditer(requirement))

        seen = set()
        result = []
        for term in terms:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                result.append(term)
        return result

    def _parse_requirement_structure(self, requirement: str, intents: list[str]) -> dict:
        """Extract lightweight grammatical roles without application-domain rules.

        Example:
          "add new attribute backupcontact in email address for employee"
        becomes:
          action=ADD, attribute=backupcontact, parent="email address", entity=employee

        This is deliberately syntax-oriented.  RAG/code scanning resolves those phrases
        to actual project types later; discovered code terms are never promoted back
        into requirement roles.
        """
        text = " ".join((requirement or "").strip().split())
        result = {
            "action": intents[0] if intents else "CHANGE",
            "attribute": None,
            "parent": None,
            "entity": None,
        }

        attr_match = re.search(
            r"\b(?:attribute|field|property|column|parameter)\s+(?:named\s+)?([A-Za-z_][A-Za-z0-9_]*)",
            text,
            re.IGNORECASE,
        )
        if attr_match:
            result["attribute"] = attr_match.group(1)

            # Parent/container phrase after the attribute: "... X in <parent> for <entity>".
            tail = text[attr_match.end():].strip()
            parent_match = re.search(
                r"\b(?:in|inside|under|within|on)\s+(.+?)(?=\s+\b(?:for|of)\b\s+|$)",
                tail,
                re.IGNORECASE,
            )
            if parent_match:
                parent = self._clean_role_phrase(parent_match.group(1))
                if parent:
                    result["parent"] = parent

        # Entity/domain phrase. Prefer an explicit trailing "for X" / "of X".
        entity_matches = list(re.finditer(
            r"\b(?:for|of)\s+(?:the\s+)?([A-Za-z_][A-Za-z0-9_-]*(?:\s+[A-Za-z_][A-Za-z0-9_-]*){0,2})\s*$",
            text,
            re.IGNORECASE,
        ))
        if entity_matches:
            result["entity"] = self._clean_role_phrase(entity_matches[-1].group(1))

        # Fallback for "Employee needs/should/has ..." style requirements.
        if not result["entity"]:
            lead = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+(?:needs?|should|must|has|have|requires?)\b", text, re.IGNORECASE)
            if lead:
                result["entity"] = lead.group(1)

        return result

    def _clean_role_phrase(self, value: str | None) -> str | None:
        if not value:
            return None
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*", value)
        while words and words[0].lower() in {"a", "an", "the", "new"}:
            words.pop(0)
        while words and words[-1].lower() in {"a", "an", "the"}:
            words.pop()
        return " ".join(words).strip() or None

    def _extract_concepts(
        self,
        requirement: str,
        tokens: set[str],
        code_terms: list[str],
        requirement_structure: dict,
    ) -> list[str]:
        # Keep requirement roles intact.  In particular, a phrase such as
        # "email address" remains one parent concept instead of independently
        # turning both "email" and "address" into endpoint subjects.
        concepts: list[str] = []
        for key in ("attribute", "parent", "entity"):
            value = requirement_structure.get(key)
            if value:
                concepts.append(value)

        concepts.extend(code_terms)

        # If grammar did not identify any roles, retain a conservative token-based
        # fallback so free-form requirements still produce useful local search.
        if not concepts:
            for raw in re.findall(r"\b[A-Za-z][A-Za-z0-9_]*\b", requirement):
                raw_parts = self._split_identifier(raw)
                if raw_parts and all(part.lower() in tokens for part in raw_parts):
                    concepts.append(raw)

        seen = set()
        deduped = []
        for item in concepts:
            key = item.lower()
            if key not in seen and key not in self.STOP_WORDS:
                seen.add(key)
                deduped.append(item)
        return deduped[:15]

    def _detect_intents(self, requirement: str) -> list[str]:
        words = {word.lower() for word in re.findall(r"[A-Za-z]+", requirement)}
        result = []
        for intent, vocabulary in self.INTENT_GROUPS.items():
            if words.intersection(vocabulary):
                result.append(intent)
        return result or ["CHANGE"]

    def _rag_search(self, requirement: str, concepts: list[str]) -> list[dict]:
        hits: list[dict] = []
        queries = [requirement]
        if concepts:
            queries.append(" ".join(concepts[:8]))

        seen = set()
        for query in queries:
            try:
                for hit in self.rag.search(query, top_k=10):
                    key = (hit.get("file_path"), hit.get("method_name"), hit.get("content", "")[:80])
                    if key not in seen:
                        seen.add(key)
                        hits.append(hit)
            except Exception:
                # Source scanning remains authoritative even if a local index is not ready.
                continue
        return hits

    def _score_files(
        self,
        project_root: Path,
        classes: Iterable,
        requirement: str,
        requirement_tokens: set[str],
        concepts: list[str],
        rag_hits: list[dict],
    ) -> dict[str, dict]:
        evidence: dict[str, dict] = {}
        rag_by_path: dict[str, list[dict]] = defaultdict(list)
        for hit in rag_hits:
            if hit.get("file_path"):
                rag_by_path[str(Path(hit["file_path"]).resolve())].append(hit)

        concept_tokens = set()
        for concept in concepts:
            concept_tokens.update(part.lower() for part in self._split_identifier(concept))
        useful_tokens = requirement_tokens | concept_tokens

        for cls in classes:
            path = Path(cls.file_path)
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            score = 0
            reasons: list[str] = []
            class_tokens = {p.lower() for p in self._split_identifier(cls.class_name)}
            class_overlap = sorted(useful_tokens.intersection(class_tokens))
            if class_overlap:
                score += 6 + len(class_overlap)
                reasons.append(f"Class name matches requirement concept: {', '.join(class_overlap)}")

            source_identifiers = {
                token.lower()
                for identifier in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", source)
                for token in self._split_identifier(identifier)
            }
            source_overlap = sorted(useful_tokens.intersection(source_identifiers))
            if source_overlap:
                weighted = min(len(source_overlap), 6)
                score += weighted
                reasons.append(f"Source contains related identifiers: {', '.join(source_overlap[:6])}")

            method_matches = []
            for method in cls.methods:
                method_tokens = {p.lower() for p in self._split_identifier(method.name)}
                if useful_tokens.intersection(method_tokens):
                    method_matches.append(method.name)
            if method_matches:
                score += min(4, len(method_matches) * 2)
                reasons.append(f"Related methods: {', '.join(method_matches[:4])}")

            resolved = str(path.resolve())
            if resolved in rag_by_path:
                score += min(8, 3 + len(rag_by_path[resolved]))
                rag_methods = [h.get("method_name") for h in rag_by_path[resolved] if h.get("method_name")]
                if rag_methods:
                    reasons.append(f"Local RAG matched methods: {', '.join(dict.fromkeys(rag_methods))}")
                else:
                    reasons.append("Local RAG matched this class")

            # Generic neighboring-type inference. If the requirement strongly identifies a
            # domain class, project types sharing its stem become supporting candidates.
            if any(self._concept_matches_class(concept, cls.class_name) for concept in concepts):
                score += 4

            if score <= 0:
                continue

            try:
                relative_path = str(path.resolve().relative_to(project_root.resolve()))
            except Exception:
                relative_path = str(path)

            evidence[resolved] = {
                "file_name": path.name,
                "file_path": resolved,
                "relative_path": relative_path,
                "class_name": cls.class_name,
                "class_type": cls.class_type,
                "score": score,
                "reasons": list(dict.fromkeys(reasons))[:5],
                "matched_methods": method_matches[:6],
            }

        return evidence

    def _concept_matches_class(self, concept: str, class_name: str) -> bool:
        concept_parts = {p.lower() for p in self._split_identifier(concept)}
        class_parts = {p.lower() for p in self._split_identifier(class_name)}
        if not concept_parts or not class_parts:
            return False
        return concept_parts.issubset(class_parts) or class_parts.issubset(concept_parts)

    def _find_affected_endpoints(
        self,
        endpoints: list[dict],
        candidate_classes: set[str],
        requirement_tokens: set[str],
        concepts: list[str],
        intents: list[str],
        requirement_structure: dict,
    ) -> list[dict]:
        """Rank endpoint impact from operation + entity + concrete flow evidence.

        The endpoint matcher deliberately avoids domain-specific names.  It treats
        HTTP verbs as protocol semantics, ignores technical path prefixes such as
        /api and /v1, and uses the entity understood from the requirement as a
        branch guard so sibling domains do not leak into the result.
        """
        results = []
        intent_set = {str(item).upper() for item in (intents or []) if item}
        entity = (requirement_structure.get("entity") or "").strip()
        entity_tokens = {p.lower() for p in self._split_identifier(entity)}
        subject_tokens = self._requirement_subject_tokens(
            requirement_tokens=requirement_tokens,
            concepts=concepts,
            requirement_structure=requirement_structure,
        )

        for endpoint in endpoints:
            score = 0
            reasons = []
            controller = endpoint.get("class_name") or ""
            method_name = endpoint.get("method_name", "")
            endpoint_text = endpoint.get("endpoint", "")
            http_method = str(endpoint.get("http_method", "")).upper()

            if http_method == "DELETE" and "REMOVE" not in intent_set:
                continue

            endpoint_tokens = set(
                self._tokenize(f"{controller} {method_name} {endpoint_text}")
            )
            overlap = sorted(subject_tokens.intersection(endpoint_tokens))
            if overlap:
                score += 3 + min(len(overlap), 3)
                reasons.append(f"Endpoint/controller matches subject: {', '.join(overlap)}")

            operation_score, operation_reason = self._endpoint_operation_score(
                http_method, method_name, intent_set
            )
            score += operation_score
            if operation_reason:
                reasons.append(operation_reason)

            nested_penalty, nested_reason = self._nested_resource_penalty(
                endpoint_text,
                subject_tokens,
                requirement_structure,
            )
            if nested_penalty <= -8:
                continue
            score += nested_penalty
            if nested_reason:
                reasons.append(nested_reason)

            flow_classes = []
            flow_methods = []
            matched = []
            entity_flow_match = False
            try:
                flow = self.endpoint_flow.analyze_endpoint(http_method, endpoint_text)
                flow_classes = self._extract_flow_classes(
                    flow.get("simplified_flow", []), flow.get("flow", [])
                )
                flow_methods = self._extract_flow_methods(
                    flow.get("simplified_flow", []), flow.get("flow", [])
                )
                matched = sorted(set(flow_classes).intersection(candidate_classes))
                if matched:
                    score += 5 + min(len(matched), 4)
                    reasons.append(f"Execution flow reaches impacted code: {', '.join(matched[:5])}")

                if entity_tokens:
                    entity_flow_match = self._flow_matches_entity(
                        flow_classes=flow_classes,
                        controller=controller,
                        method_name=method_name,
                        endpoint=endpoint_text,
                        entity_tokens=entity_tokens,
                    )
                    if entity_flow_match:
                        score += 4
                        reasons.append(f"Execution flow belongs to requirement entity: {entity}")
            except Exception:
                pass

            # If the requirement identifies a domain entity, an endpoint from a
            # sibling branch is not considered affected merely because it shares a
            # base controller/service/mapper.
            if entity_tokens and not entity_flow_match:
                continue

            business_rule = "BUSINESS_RULE" in intent_set
            semantic_evidence = bool(overlap) or operation_score > 0 or business_rule
            concrete_flow_evidence = bool(matched) or entity_flow_match

            # An ADD field normally affects create and update representations.  A
            # BUSINESS_RULE may have no explicit CRUD verb, so entity + flow evidence
            # is the deciding signal in that case.
            threshold = 7 if business_rule else 8
            if score >= threshold and semantic_evidence and concrete_flow_evidence:
                relevance = "PRIMARY" if score >= 13 else "SECONDARY"
                results.append({
                    "http_method": http_method,
                    "endpoint": endpoint_text,
                    "controller": controller,
                    "method_name": method_name,
                    "score": score,
                    "relevance": relevance,
                    "reasons": list(dict.fromkeys(reasons)),
                    "flow_classes": flow_classes[:30],
                    "flow_methods": flow_methods[:40],
                    "matched_classes": matched[:12],
                })

        results.sort(
            key=lambda item: (
                0 if item.get("relevance") == "PRIMARY" else 1,
                -item["score"],
                item["endpoint"],
                item["http_method"],
            )
        )
        return results[:12]

    def _endpoint_operation_score(
        self, http_method: str, method_name: str, intents: set[str]
    ) -> tuple[int, str | None]:
        method_tokens = {p.lower() for p in self._split_identifier(method_name)}

        if http_method == "GET":
            if "READ" in intents:
                return 6, "HTTP GET matches requested READ behaviour"
            if "BUSINESS_RULE" in intents:
                return 1, "GET may expose the result of the business rule"
            return -2, None

        if http_method == "POST":
            if intents.intersection({"ADD", "PERSIST"}):
                return 6, "HTTP POST matches requested create/persist behaviour"
            if "BUSINESS_RULE" in intents:
                return 2, "POST may evaluate the rule while creating the entity"
            return 0, None

        if http_method in {"PUT", "PATCH"}:
            if "UPDATE" in intents:
                return 6, f"HTTP {http_method} matches requested UPDATE behaviour"
            if "PERSIST" in intents and "ADD" not in intents:
                return 3, f"HTTP {http_method} may participate in persistence"
            if "ADD" in intents:
                # Newly introduced fields commonly need to travel through full or
                # partial update requests even if the Jira prose only says "add".
                return 3, f"HTTP {http_method} may need to carry the new attribute"
            if "BUSINESS_RULE" in intents:
                return 3, f"HTTP {http_method} may change data used by the business rule"
            if method_tokens.intersection({"update", "replace", "patch"}):
                return 1, None
            return -1, None

        if http_method == "DELETE":
            if "REMOVE" in intents:
                return 6, "HTTP DELETE matches requested REMOVE behaviour"
            return -10, "DELETE conflicts with the requested change intent"

        return 0, None

    def _requirement_subject_tokens(
        self,
        requirement_tokens: set[str],
        concepts: list[str],
        requirement_structure: dict,
    ) -> set[str]:
        result = set(requirement_tokens)
        for value in concepts or []:
            result.update(p.lower() for p in self._split_identifier(str(value)))
        for key in ("entity", "attribute", "parent"):
            value = requirement_structure.get(key)
            if value:
                result.update(p.lower() for p in self._split_identifier(str(value)))
        return {token for token in result if token and token not in self.STOP_WORDS}

    def _literal_resource_segments(self, endpoint: str) -> list[str]:
        technical = {
            "api", "rest", "service", "services", "public", "internal",
        }
        result = []
        for segment in endpoint.strip("/").split("/"):
            if not segment or (segment.startswith("{") and segment.endswith("}")):
                continue
            low = segment.lower()
            if low in technical or re.fullmatch(r"v\d+", low):
                continue
            result.append(segment)
        return result

    def _nested_resource_penalty(
        self,
        endpoint: str,
        requirement_tokens: set[str],
        requirement_structure: dict,
    ) -> tuple[int, str | None]:
        literal_segments = self._literal_resource_segments(endpoint)

        # One business-resource segment means a root collection/item endpoint.
        # /api/persons therefore has no nested-resource penalty.
        if len(literal_segments) <= 1:
            return 0, None

        child = literal_segments[-1]
        child_tokens = set(self._tokenize(child.replace("-", " ")))
        if not child_tokens:
            return 0, None

        parent_phrase = requirement_structure.get("parent")
        if parent_phrase:
            parent_parts = [
                p.lower() for p in self._split_identifier(parent_phrase)
                if p.lower() not in self.STOP_WORDS
            ]
            parent_tokens = set(parent_parts)
            overlap = child_tokens.intersection(parent_tokens)

            if len(parent_parts) > 1:
                qualifier_tokens = set(parent_parts[:-1])
                qualifier_overlap = child_tokens.intersection(qualifier_tokens)
                if qualifier_overlap:
                    return 5, (
                        "Nested resource matches parent qualifier: "
                        + ", ".join(sorted(qualifier_overlap))
                    )
                if overlap:
                    return -6, (
                        f"Nested resource '{child}' matches only the broad part of "
                        f"parent phrase '{parent_phrase}'"
                    )
                return -9, f"Nested resource '{child}' does not match parent phrase '{parent_phrase}'"

            if overlap:
                return 4, f"Nested resource matches parent concept: {', '.join(sorted(overlap))}"
            return -9, f"Nested resource '{child}' does not match parent concept '{parent_phrase}'"

        overlap = child_tokens.intersection(requirement_tokens)
        coverage = len(overlap) / len(child_tokens)
        if coverage >= 0.5:
            return 3, f"Nested resource matches requirement subject: {', '.join(sorted(overlap))}"

        return -8, f"Nested resource '{child}' is not part of the requirement subject"

    def _flow_matches_entity(
        self,
        flow_classes: list[str],
        controller: str,
        method_name: str,
        endpoint: str,
        entity_tokens: set[str],
    ) -> bool:
        evidence_tokens = set()
        for value in [controller, method_name, endpoint, *flow_classes]:
            evidence_tokens.update(
                p.lower() for p in self._split_identifier(str(value or ""))
            )
        return bool(entity_tokens.intersection(evidence_tokens))

    def _extract_flow_classes(self, simplified_flow, full_flow) -> list[str]:
        names = []

        def walk(value):
            if isinstance(value, dict):
                for key in ("class_name", "class", "source_class", "target_class"):
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate:
                        names.append(candidate)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str):
                match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)[.#:]", value)
                if match:
                    names.append(match.group(1))

        walk(simplified_flow)
        walk(full_flow)
        return list(dict.fromkeys(names))

    def _extract_flow_methods(self, simplified_flow, full_flow) -> list[str]:
        methods = []

        def add(class_name, method_name):
            if class_name and method_name:
                value = f"{class_name}.{method_name}"
                if value not in methods:
                    methods.append(value)

        def walk(value):
            if isinstance(value, dict):
                add(value.get("class_name"), value.get("method_name"))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
            elif isinstance(value, str):
                match = re.match(
                    r"([A-Za-z_][A-Za-z0-9_]*)[.#:]([A-Za-z_][A-Za-z0-9_]*)",
                    value,
                )
                if match:
                    add(match.group(1), match.group(2))

        walk(simplified_flow)
        walk(full_flow)
        return methods

    def _find_affected_scenarios(
        self,
        scenarios,
        affected_endpoints: list[dict],
        candidate_classes: set[str],
        concepts: list[str],
        requirement_structure: dict,
    ) -> list[dict]:
        endpoint_keys = {
            (str(item.get("http_method", "")).upper(), str(item.get("endpoint", "")))
            for item in affected_endpoints
        }
        concept_tokens = {p.lower() for c in concepts for p in self._split_identifier(c)}
        entity = (requirement_structure.get("entity") or "").strip()
        entity_tokens = {p.lower() for p in self._split_identifier(entity)}

        results = []
        for scenario in scenarios:
            score = 0
            reasons = []
            key = (str(scenario.http_method).upper(), str(scenario.endpoint))

            involved = set()
            if scenario.involved_classes:
                raw = scenario.involved_classes
                if isinstance(raw, str):
                    values = re.split(r"[,;\n]", raw)
                else:
                    values = list(raw)
                involved = {
                    str(item).strip().split(".")[-1]
                    for item in values
                    if str(item).strip()
                }

            scenario_tokens = self._tokenize(
                f"{scenario.scenario_code} {scenario.scenario_name} {scenario.description or ''}"
            )

            if entity_tokens:
                involved_tokens = {
                    token.lower()
                    for class_name in involved
                    for token in self._split_identifier(class_name)
                }
                if not entity_tokens.intersection(scenario_tokens | involved_tokens):
                    continue
                reasons.append(f"Scenario belongs to requirement entity: {entity}")

            if key in endpoint_keys:
                score += 10
                reasons.append("Scenario uses an affected endpoint")

            matched = sorted(involved.intersection(candidate_classes)) if involved else []
            if matched:
                score += 5
                reasons.append(f"Scenario includes impacted classes: {', '.join(matched[:5])}")

            overlap = sorted(concept_tokens.intersection(scenario_tokens))
            if overlap:
                score += 2 + min(len(overlap), 4)
                reasons.append(f"Scenario description matches: {', '.join(overlap)}")

            # Do not let a weak shared-class match create an affected scenario when
            # the endpoint itself is known and not affected.
            if score >= 5:
                results.append({
                    "id": scenario.id,
                    "scenario_code": scenario.scenario_code,
                    "scenario_name": scenario.scenario_name,
                    "http_method": scenario.http_method,
                    "endpoint": scenario.endpoint,
                    "score": score,
                    "reasons": list(dict.fromkeys(reasons)),
                })

        results.sort(key=lambda item: (-item["score"], item["scenario_code"]))
        return results[:20]

    def _build_dependency_paths(
        self,
        affected_endpoints: list[dict],
        candidate_classes: set[str],
        concepts: list[str],
    ) -> list[dict]:
        paths = []
        concept_label = concepts[0] if concepts else "Requirement"

        for endpoint in affected_endpoints[:8]:
            flow_methods = endpoint.get("flow_methods") or []
            flow_classes = endpoint.get("flow_classes") or []
            matched_classes = set(endpoint.get("matched_classes") or [])

            relevant_methods = [
                method for method in flow_methods
                if method.split(".", 1)[0] in candidate_classes
                or method.split(".", 1)[0] in matched_classes
            ]
            if not relevant_methods:
                relevant_methods = flow_methods[:6]

            chain = [concept_label]
            chain.extend(relevant_methods[:8])
            if not relevant_methods:
                chain.extend([name for name in flow_classes if name in candidate_classes][:6])
            endpoint_label = f"{endpoint['http_method']} {endpoint['endpoint']}"
            chain.append(endpoint_label)
            chain = list(dict.fromkeys(item for item in chain if item))

            if len(chain) > 2:
                paths.append({
                    "endpoint": endpoint_label,
                    "path": chain,
                    "matched_classes": sorted(matched_classes),
                })
        return paths

    def _confidence(self, files, endpoints, scenarios) -> dict:
        score = 0
        if files:
            score += min(45, 20 + files[0].get("score", 0) * 2)
        if endpoints:
            score += 30
        if scenarios:
            score += 20
        score = min(score, 95)
        level = "HIGH" if score >= 75 else "MEDIUM" if score >= 45 else "LOW"
        return {
            "score": score,
            "level": level,
            "note": "Impact is evidence-based and advisory; validate suggested locations before implementation."
        }
