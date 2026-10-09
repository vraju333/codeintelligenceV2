from __future__ import annotations

import os
import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from sqlalchemy.orm import Session

from config import settings
from services.agent.knowledge_tool_registry import KnowledgeToolRegistry


SYSTEM_PROMPT = """You are the CodeIntelligence Knowledge Agent.
Use the registered tools to answer questions about engineering knowledge.
Choose tools dynamically from their descriptions; do not call every tool by default.

LIVE JIRA RULES:
- live_jira_issue is authoritative whenever the user explicitly asks about "live Jira", "current Jira", or a live Jira issue key.
- Never claim a live Jira requirement was analyzed unless live_jira_issue returned that issue.
- After reading a live Jira requirement, analyze its named business attributes against CURRENT source code with static_code_analysis and scenario_impact when source/scenario impact is requested.
- Treat existing dirty Git changes as separate evidence. They prove implementation of the live Jira only when the changed symbols/attributes actually match the live requirement.
- If the live Jira introduces an attribute that static_code_analysis cannot find, say NOT IMPLEMENTED / NOT FOUND IN CURRENT SOURCE rather than treating unrelated Git changes as implementation.
- Implementation status must come from current source/static evidence, NOT from whether changes are committed or uncommitted.
- Existing baselines/JUnit evidence for other Jira IDs are historical/unrelated evidence, not proof for the live Jira.
- For live-Jira analysis, a release/test baseline counts as CURRENT Jira evidence only when the test baseline explicitly contains that Jira ID. A baseline attached to the same scenario but to another Jira must be labeled historical and must not close the current Jira baseline gap.

KNOWLEDGE RULES:
- Use graph_search for exact engineering-entity relationship/impact/trace/history questions.
- For "why was <Class> created", "which Jira introduced <Class>", or class-purpose questions, retrieve current Java source, cross-source JIRA/requirement evidence and graph relationships. Do NOT substitute current Git diff for historical authorship.
- A class matching a Jira requirement is an INFERRED CANDIDATE, not a confirmed creation reason. Only explicit Jira references or verified historical records can establish authorship.
- Never explain missing scenario coverage as caused by uncommitted Git changes; scenario coverage is independently captured and linked.
- SCENARIO EVIDENCE LEVELS: a discovered API scenario is VERIFIED AS EXISTING only. It is NOT verified as covering an attribute or Jira. A scenario_impact result is a POTENTIALLY IMPACTED CANDIDATE unless explicit test assertions, captured test evidence or scenario-to-Jira/baseline links prove coverage. Never put such candidates under "Verified Test Scenarios" or say "confirmed coverage".
- When the Jira scenario/baseline lookup returns no explicit links, retain "confirmed Jira coverage: none found" even if other tools return 6 API operations or attribute-level candidates.
- A change to input validation does not automatically impact GET, DELETE or account-type PATCH endpoints; require concrete call/data-flow evidence or label as low-confidence candidates.
- For CURRENT Java implementation, java_source_search exact working-tree evidence is authoritative; an indexed code snippet without a matching live source path/fingerprint is historical or UNVERIFIED CURRENT, not confirmed current.
- For Phase 7 source tools, never pass a project folder name such as customer-account-service as a filesystem path; use the selected project's configured absolute path.
- For mapping document questions, prefer exact mapping_lineage rows with filename, sheet, row and target over semantic search.
- For attribute impact, relevant JIRA requirements are inferred implementation candidates unless an explicit reference is verified.
- No matches in one tool do not prove that another registry has no evidence.
- A verified JIRA document is NOT a verified JIRA-to-class link. Label requirement existence VERIFIED and class association INFERRED separately; never summarize an inferred link as "Verified JIRA".
- For attribute impact questions, retrieve exact mapping rows and JIRA/requirement candidates even when no explicit JIRA graph edge exists. Do not claim no related JIRA solely because explicit links are zero.
- Document metadata (document ID, workbook filename, sheet, row, source path, target) is evidence; do not invent a download URL or claim the original workbook is available without a verified source path.
- Documented XML-to-Customer mappings must not be silently relabeled as XML-to-CustomerXmlRequest mappings. State the precise source/target of each mapping.
- Missing Jira-method edges or no current Git changes means "historical link unverified", not "class has no related requirements".
- Use mapping_lineage when the starting point is a Java/business attribute.
- Use mapping_document_rows for a named .xlsx/.xlsm workbook and return every authoritative mapping row; workbook names are NOT source XPaths.
- Use mapping_source_lineage when the starting point is an XML path/node, JSON path/node, DB table.column, Kafka field, or other external source path. It resolves source -> Java target and preserves workbook/sheet/row evidence.
- For source-side impact questions, call mapping_source_lineage with include_engineering_impact=true instead of guessing the Java attribute.
- Use mapping_history for mapping evolution/history across versions and mapping_compare for explicit version-to-version comparisons.
- Use mapping_validation when a mapping document/version target must be checked against current code.
- Use mapping_change_impact for mapping-change blast radius across diff, code validity and engineering relationships.
- Use mapping_quality for conflicts, duplicate definitions, inconsistent rules and missing code targets.
- Use mapping_intelligence for a consolidated latest/previous mapping view; it is deterministic and preserves provenance.
- For latest/current/previous mapping validation questions, the deterministic router resolves the document ID. Never guess or substitute a document_id from mapping lineage/history.
- Use mapping_graph_lineage when the question spans mapping evidence AND downstream engineering impact (methods/endpoints/scenarios/JIRAs/releases/test baselines).
- Mapping answers must cite the returned document, version, sheet and row; never invent mapping provenance.
- When a mapping-history result introduces a changed Java target and the user asks whether it is valid/currently implemented, validate the corresponding document with mapping_validation.
- For non-mapping impact + baseline-history questions, combine graph_search with scenario_impact and baseline_history so Neo4j relationship evidence is not skipped.
- Prefer rag_search for semantic/document discovery questions.
- Prefer unified_knowledge_search for complete cross-source impact or 'everything about' questions.
- When a question explicitly asks for all available engineering knowledge, documented knowledge, requirements, releases, architecture, API/schema documentation, or ingested test evidence, unified_knowledge_search is mandatory.
- When such a cross-source question also contains an exact Jira ID, graph_search is also mandatory.
- Do not treat Git/SDLC, static analysis, or baseline evidence as a substitute for ingested knowledge documents.

AI ENGINEERING ASSISTANT RULES:
- Use engineering_assistant when the user asks about CURRENT/current changes, including where a changed attribute comes from, its JIRA relationship, impacted scenarios, regression testing, developer review, or a consolidated test/release action plan.
- For a current-change question, treat engineering_assistant as the primary consolidated evidence. Do not override it with narrower legacy Git results. Its mapping_data_lineage resolves changed Java target attributes and preserves mapping family/version/document/sheet/row provenance.
- The engineering_assistant result is an evidence-grounded orchestration view over current-change intelligence; preserve exact entity names, test evidence, risks and release-readiness status.
- JIRA SEMANTICS ARE STRICT: `impacted_historical_jiras` / compatibility field `confirmed_jiras` are historical/impacted traceability only. NEVER describe them as the Jira for the current uncommitted change. `current_change_jira_candidates` are the only inferred current-change Jira candidates and remain unconfirmed/non-authoritative unless separate current-change evidence confirms them. If a candidate exists, report its exact classification and confidence.
- If KAN-4 is historical while STUD-101 is a LIKELY_CURRENT_JIRA candidate, say exactly that distinction; never call KAN-4 the likely/current Jira.
- MAPPING SEMANTICS ARE STRICT: mapping version recency and code validity are separate. `version_status=LATEST` does not mean the mapping is valid or matching. Preserve `version_status`, `target_status`, `conflict_status`, and `classification`; a LATEST mapping with TARGET_NOT_FOUND must be described as latest but invalid, not current/matching.
- Do not turn recommendations into claims that tests passed, requirements were approved, Jira links were proven, or release was approved.
- Prefer engineering_assistant over manually composing many lower-level tools when the request is explicitly for a consolidated developer action plan.

REGRESSION & RELEASE INTELLIGENCE RULES:
- Use release_regression_intelligence for CURRENT-change regression recommendations, coverage gaps and release-readiness questions.
- Release intelligence composes current Git/source impact, scenarios, captured test baselines, static test evidence, Phase 7 deep-code evidence, Phase 6 mappings and Phase 8 discovery.
- Never describe READY_FOR_RELEASE_REVIEW as automatic release approval. Preserve BLOCKED, REVIEW_REQUIRED, READY_FOR_RELEASE_REVIEW or NO_CHANGES exactly.
- Phase 8 evidence inside release intelligence remains discovery evidence; do not promote semantic matches into proven relationships.

PHASE 8 ENTERPRISE HYBRID RAG RULES:
- Use enterprise_hybrid_search for broad semantic/lexical discovery across engineering knowledge.
- Use java_source_search for exact Java class/method signatures, implementations and execution flows; its exact current-source evidence is authoritative, while pgvector matches are discovery only.
- Phase 8 uses PostgreSQL pgvector + BM25 + metadata filtering + deterministic reranking; it does not use FAISS.
- Retrieval results are candidate discovery evidence, not authoritative relationship proof. Verify exact relationships with graph/mapping/static/baseline tools when required.
- Preserve source_type, title and metadata returned by enterprise_hybrid_search.

PHASE 7 DEEP CODE INTELLIGENCE RULES:
- Use control_flow_analysis for branch/condition/threshold/decision questions about an attribute.
- Use data_flow_analysis for assignment, transformation, value propagation, source-to-target and cross-method flow questions.
- Use shared_component_impact for reused mapper/helper/common-component ripple-effect questions.
- Use deep_code_intelligence for broad deeper-code/everything-about-flow questions.
- Phase 7 source evidence is deterministic. Preserve condition expressions, qualified method names, files and line numbers exactly.
- Never infer a branch or data-flow edge that is not present in Phase 7 tool evidence.

CURRENT SOURCE / SDLC RULES:
- Use static_code_analysis for current source-code impact of an attribute.
- Use scenario_impact for affected scenario/operation questions.
- Use git_scenario_impact when the user asks which scenarios/endpoints/operations are impacted by CURRENT Git changes.
- Use attribute_test_evidence when the user asks which tests cover/validate/provide evidence for an attribute; this is independent of baselines.
- Use baseline_history for previous testing, release or baseline-history questions.
- Use git_sdlc_traceability for end-to-end CURRENT Git SDLC closure/traceability.
- For questions asking which requirements/JIRAs relate to CURRENT Git changes, use git_requirement_correlation.
- Use git_change_analysis only when the user asks what changed without asking for requirement/Jira correlation.
- Never report a requirement/Jira as related merely because RAG returned it or because it is semantically similar.
- Candidate-only evidence must remain explicitly unconfirmed.

For questions spanning live Jira, current code, scenarios and testing, combine the relevant evidence and clearly separate:
1. LIVE REQUIREMENT
2. CURRENT SOURCE-CODE EVIDENCE
3. CURRENT GIT EVIDENCE
4. SCENARIO / TEST / BASELINE EVIDENCE
5. TRACEABILITY GAPS

JIRA IMPLEMENTATION EVIDENCE:
- The Java static analyzer verifies current source occurrences, not historical Jira authorship.
- Never claim a Jira-to-code link from attribute-name overlap alone.
- Only show persistence methods and HTTP routes explicitly evidenced by static tools.
- A graph NO_MATCH must be reported as missing graph traceability.

ENDPOINT CLASSIFICATION RULES:
- A retrieved document's `entities.endpoints` is NOT authoritative HTTP-route evidence.
  This includes stale persisted RAG metadata; reject XPath-like entries from
  mapping documents regardless of what their metadata calls them.
- HTTP routes require an HTTP method (GET/POST/PUT/PATCH/DELETE) and a Java
  Spring mapping annotation or static analyzer evidence. If absent, report
  "REST endpoints not verified" rather than listing XML paths.
- An XML XPath is mapping input evidence only, even when it begins with '/'.
- XML XPaths (such as /CustomerAccount/Email) are NOT HTTP API endpoints.
- Report HTTP endpoints only from explicit HTTP route declarations or verified Java static analysis.
- Jira issue keys are not Java attributes. Never pass a Jira key as mapping_lineage.attribute.
- A graph NO_MATCH is a missing relationship, not proof that Java implementation is absent.
- Do not infer a database table/column from the name of a Java class.

EVIDENCE-GROUNDING RULES:
- Base the final answer only on tool evidence actually returned in this request.
- Do not create headings or claims for Live Requirement, Current Git Evidence, Test/Baseline Evidence, or any other evidence category unless a returned tool result supports that category.
- Preserve exact engineering entity names returned by tools. Never rename an owner/class/method (for example, StudentPromotionController.checkPromotion must remain exactly that).
- Mapping-only answers should prefer evidence-appropriate sections such as Mapping Evidence, Code Validation, Engineering Impact, Mapping Conflicts/Quality, Provenance, Risks and Traceability Gaps.

Base the final answer only on returned tool evidence. Be concise and name the evidence sources used.
"""


class KnowledgeAgentService:
    """LangGraph tool-calling agent. The model chooses tools; LangGraph executes the loop."""

    def run(self, db: Session, query: str) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query is required")

        registry = KnowledgeToolRegistry(db)
        tools = registry.tools()

        # Deterministic routing guarantees authoritative evidence sources that the
        # LLM must not be allowed to skip. After these calls, the normal LangGraph
        # agent loop remains dynamic and can choose additional tools from the results.
        mandatory_calls = self._mandatory_calls(query, tools, registry)

        # If a relative mapping-validation question has already been resolved to an
        # authoritative document ID, do not expose mapping_validation to the LLM for
        # the same turn. Otherwise the model can issue a second guessed document_id
        # (for example V1/document 1) and override/confuse the deterministic V3 call.
        deterministic_relative_validation = any(
            call.get("id") == "mandatory_phase6_relative_mapping_validation"
            for call in mandatory_calls
        )
        deterministic_phase8_tools = {
            str(call.get("name"))
            for call in mandatory_calls
            if str(call.get("id") or "").startswith("mandatory_phase8_")
        }
        deterministic_release_tools = {
            str(call.get("name"))
            for call in mandatory_calls
            if str(call.get("id") or "").startswith("mandatory_release_")
        }
        deterministic_phase7_tools = {
            str(call.get("name"))
            for call in mandatory_calls
            if str(call.get("id") or "").startswith("mandatory_phase7_")
        }
        # A deterministic Phase 7 call is authoritative for the requested source
        # semantic.  Do not let the model "rescue" that same turn with the legacy
        # static analyzer; otherwise a bad Phase 7 extraction can be hidden by a
        # second LLM-selected static_code_analysis call.
        phase7_dynamic_block = set(deterministic_phase7_tools) | set(deterministic_phase8_tools) | set(deterministic_release_tools)
        if deterministic_phase7_tools:
            phase7_dynamic_block.add("static_code_analysis")

        # Current-change questions use Engineering Assistant as the authoritative
        # aggregator. Block narrower legacy tools that can contradict its normalized
        # code/scenario evidence or misread a Java target attribute as a source path.
        if any(call.get("name") == "engineering_assistant" for call in mandatory_calls):
            phase7_dynamic_block.update({
                "git_requirement_correlation",
                "git_scenario_impact",
                "mapping_source_lineage",
            })

        dynamic_tools = [
            tool for tool in tools
            if not (deterministic_relative_validation and tool.name == "mapping_validation")
            and tool.name not in phase7_dynamic_block
        ]
        model = self._build_model().bind_tools(dynamic_tools)

        def router_node(state: MessagesState):
            if not mandatory_calls:
                return {"messages": []}
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=mandatory_calls,
                    )
                ]
            }

        def enrichment_node(state: MessagesState):
            # This node runs AFTER mandatory Live Jira/Git tools have returned.
            # It deterministically derives attributes from the authoritative Jira
            # payload and emits static/scenario tool calls.
            enrichment_calls = self._live_jira_enrichment_calls(state["messages"], tools)
            already_called = {
                str(call.get("id"))
                for message in state["messages"]
                if isinstance(message, AIMessage)
                for call in (getattr(message, "tool_calls", None) or [])
                if isinstance(call, dict)
            }
            pending = [call for call in enrichment_calls if call["id"] not in already_called]
            if not pending:
                return {"messages": []}
            return {"messages": [AIMessage(content="", tool_calls=pending)]}

        def agent_node(state: MessagesState):
            response = model.invoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
            return {"messages": [response]}

        # LangGraph ToolNode executes multiple calls concurrently. The registry's
        # tools share the request-scoped SQLAlchemy Session, which is not safe
        # for concurrent use. Execute each tool call in order instead.
        def sequential_tools_node(state: MessagesState):
            last = state["messages"][-1]
            calls = getattr(last, "tool_calls", None) or []
            if not calls:
                return {"messages": []}
            responses = []
            for call in calls:
                single_call = AIMessage(content="", tool_calls=[call])
                result = ToolNode(tools).invoke({"messages": [single_call]})
                responses.extend(result.get("messages", []))
            return {"messages": responses}

        builder = StateGraph(MessagesState)
        builder.add_node("router", router_node)
        builder.add_node("mandatory_tools", sequential_tools_node)
        builder.add_node("enrichment", enrichment_node)
        builder.add_node("enrichment_tools", sequential_tools_node)
        builder.add_node("agent", agent_node)
        builder.add_node("agent_tools", sequential_tools_node)

        builder.add_edge(START, "router")

        # Guaranteed pre-synthesis path:
        # router -> live Jira/current Git -> enrichment -> static/scenario -> agent
        if mandatory_calls:
            builder.add_edge("router", "mandatory_tools")
            builder.add_edge("mandatory_tools", "enrichment")
            builder.add_conditional_edges(
                "enrichment",
                tools_condition,
                {"tools": "enrichment_tools", END: "agent"},
            )
            builder.add_edge("enrichment_tools", "agent")
        else:
            builder.add_edge("router", "agent")

        # Normal dynamic agent loop remains available after guaranteed evidence.
        builder.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "agent_tools", END: END},
        )
        builder.add_edge("agent_tools", "agent")
        graph = builder.compile()

        result = graph.invoke({"messages": [HumanMessage(content=query)]})
        messages = result.get("messages", [])
        tool_calls = []
        tool_results = []
        for message in messages:
            if isinstance(message, AIMessage):
                for call in message.tool_calls or []:
                    tool_calls.append({
                        "name": call.get("name"),
                        "args": call.get("args") or {},
                        "id": call.get("id"),
                    })
            if getattr(message, "type", "") == "tool":
                tool_results.append({
                    "name": getattr(message, "name", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                    "content": getattr(message, "content", ""),
                })

        final_answer = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage) and not (message.tool_calls or []):
                final_answer = self._message_text(message)
                if final_answer:
                    break

        return {
            "query": query,
            "provider": (settings.LLM_PROVIDER or "ollama").strip().lower(),
            "tools_available": [tool.name for tool in tools],
            "tools_selected": [call["name"] for call in tool_calls],
            "tool_calls": tool_calls,
            "answer": final_answer,
            "tool_results": tool_results,
        }

    @staticmethod
    def _live_jira_enrichment_calls(messages: list[Any], tools: list[Any]) -> list[dict[str, Any]]:
        """Turn a returned Live Jira requirement into grounded current-code evidence.

        This intentionally uses the authoritative Jira payload rather than the
        user's wording. For "Add a new attribute named X" requirements we can
        deterministically identify X and verify it in current code/scenarios.
        """
        available = {tool.name for tool in tools}
        payloads: list[dict[str, Any]] = []

        for message in messages:
            # ToolMessage is intentionally not imported just for isinstance:
            # LangChain tool results expose name/content consistently.
            if getattr(message, "name", None) != "live_jira_issue":
                continue
            raw = getattr(message, "content", "")
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)

        attributes: list[str] = []
        for payload in payloads:
            issue = payload.get("issue") if isinstance(payload, dict) else None
            if not isinstance(issue, dict):
                continue
            requirement = " ".join(
                str(issue.get(key) or "")
                for key in ("summary", "description")
            )

            # Primary deterministic pattern for our Jira requirements:
            # "Add a new attribute named preferredLanguage ..."
            patterns = (
                r"\battribute\s+named\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)",
                r"\bfield\s+named\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)",
                r"\badd\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?\s+to\b",
            )
            for pattern in patterns:
                for match in re.findall(pattern, requirement, flags=re.IGNORECASE):
                    if match and match.lower() not in {
                        "a", "an", "the", "new", "string", "student", "address"
                    }:
                        attributes.append(match)

        # Also recognize enumerated business fields in a live Jira description.
        # They are source-analysis candidates, not proven implementation links.
        for payload in payloads:
            issue = payload.get("issue") if isinstance(payload, dict) else None
            if not isinstance(issue, dict):
                continue
            description = str(issue.get("description") or "")
            lines = description.splitlines()
            for position, line in enumerate(lines):
                if not re.search(r"\bcontains\s*:\s*$", line, re.I):
                    continue
                for item in lines[position + 1:position + 16]:
                    item = item.strip().lstrip("-* ")
                    if not item:
                        break
                    if not re.fullmatch(r"[A-Za-z]+(?:\s+[A-Za-z]+){0,3}", item):
                        break
                    parts = item.split()
                    attributes.append(parts[0].lower() + "".join(p.title() for p in parts[1:]))
                break

        # XML element declarations in Jira often enumerate fields without saying
        # "attribute named". They are candidates for current-code verification,
        # never proof of a Jira-to-code relationship.
        for payload in payloads:
            issue = payload.get("issue") if isinstance(payload, dict) else None
            if not isinstance(issue, dict):
                continue
            description = str(issue.get("description") or "")
            # Explicit XML tags / paths are unambiguous business field names.
            for field in re.findall(r"/CustomerAccount/([A-Za-z][A-Za-z0-9_]*)", description, re.I):
                attributes.append(field[0].lower() + field[1:])
            # Support bullet enumerations like "- customerId", "* Country Code".
            in_fields = False
            for line in description.splitlines():
                if re.search(r"\b(?:fields?|attributes?|elements?)\s*(?:include|contains?|are|:)\s*:?$", line, re.I):
                    in_fields = True
                    continue
                if not in_fields:
                    continue
                match = re.match(r"^\s*(?:[-*•]|\d+[.)])\s*([A-Za-z][A-Za-z0-9_]*(?:\s+[A-Za-z]+){0,2})\s*$", line)
                if not match:
                    if line.strip():
                        in_fields = False
                    continue
                words = match.group(1).split()
                candidate = words[0][0].lower() + words[0][1:] + "".join(word.title() for word in words[1:])
                if candidate.casefold() not in {"xml", "database", "validation", "required"}:
                    attributes.append(candidate)

        # Preserve order and spelling while preventing duplicate tool calls.
        attributes = list(dict.fromkeys(attributes))[:20]
        calls: list[dict[str, Any]] = []
        for index, attribute in enumerate(attributes, start=1):
            safe_id = re.sub(r"[^A-Za-z0-9]+", "_", attribute).strip("_").lower()
            if "static_code_analysis" in available:
                calls.append({
                    "name": "static_code_analysis",
                    "args": {"attribute": attribute},
                    "id": f"live_jira_static_{index}_{safe_id}",
                })
            if "scenario_impact" in available:
                calls.append({
                    "name": "scenario_impact",
                    "args": {"attribute": attribute},
                    "id": f"live_jira_scenario_{index}_{safe_id}",
                })
        return calls

    @staticmethod
    def _extract_graph_entity(query: str) -> str | None:
        """Extract a concrete engineering entity from a relationship-style question.

        This is deliberately conservative. It supports quoted/backticked symbols,
        Jira IDs, and the common "for/of <identifier>" wording used by the
        CodeIntelligence UI (for example: "baseline history for gpa").
        """
        text = str(query or "").strip()
        if not text:
            return None

        jira = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", text.upper())
        if jira:
            return jira.group(0)

        quoted = re.findall(r"[`'\"]([A-Za-z_][A-Za-z0-9_.$/-]*)[`'\"]", text)
        if quoted:
            return quoted[-1].rstrip(".,?!:;")

        patterns = (
            r"(?:for|of|about|on)\s+([A-Za-z_][A-Za-z0-9_.$/-]*)[?.!]*\s*$",
            r"(?:impact|history|trace|dependencies|relationships?)\s+(?:for|of)\s+([A-Za-z_][A-Za-z0-9_.$/-]*)",
        )
        stop = {"the", "this", "that", "code", "scenario", "baseline", "history", "impact"}
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                entity = match.group(1).rstrip(".,?!:;")
                if entity and entity.lower() not in stop:
                    return entity
        return None

    @staticmethod
    def _extract_mapping_family(query: str) -> str | None:
        """Extract an explicitly named mapping family without treating it as an attribute.

        Examples: "Student Mapping", "Customer Address Mapping". Relative-version
        words and common question prefixes are stripped from the candidate.
        """
        text = str(query or "").strip()
        if not text:
            return None

        matches = re.findall(r"\b([A-Za-z][A-Za-z0-9 _-]{0,80}?\s+Mapping)\b", text, flags=re.IGNORECASE)
        if not matches:
            return None

        candidate = matches[-1].strip()
        prefix = r"^(?:what|which|show|tell|give|compare|changed|change|changes|in|for|of|the|latest|current|newest|previous|prior)\s+"
        while re.match(prefix, candidate, flags=re.IGNORECASE):
            candidate = re.sub(prefix, "", candidate, count=1, flags=re.IGNORECASE).strip()
        return candidate or None

    @staticmethod
    def _relative_mapping_versions(
        family: str,
        tools: list[Any],
    ) -> tuple[str | None, str | None]:
        """Resolve previous/latest versions from authoritative mapping history.

        This is a routing metadata lookup only. The actual user-visible evidence still
        comes from mapping_compare/mapping_validation tool calls executed by LangGraph.
        """
        history_tool = next((tool for tool in tools if getattr(tool, "name", None) == "mapping_history"), None)
        if history_tool is None:
            return None, None

        seed_attribute = family[:-len(" Mapping")].strip() if family.lower().endswith(" mapping") else family
        try:
            payload = history_tool.invoke({"attribute": seed_attribute, "mapping_family": family})
        except Exception:
            return None, None

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None, None
        if not isinstance(payload, dict):
            return None, None

        timelines = payload.get("timelines") or []
        timeline = next(
            (item for item in timelines if str(item.get("mapping_family") or "").lower() == family.lower()),
            None,
        )
        if not isinstance(timeline, dict):
            return None, None

        versions = []
        for item in timeline.get("versions") or []:
            document = item.get("document") if isinstance(item, dict) else None
            version = str((document or {}).get("document_version") or "").strip().upper()
            if version:
                versions.append(version)

        def version_key(value: str):
            nums = re.findall(r"\d+", value)
            return tuple(int(n) for n in nums) if nums else (0,)

        versions = sorted(set(versions), key=version_key)
        if not versions:
            return None, None
        latest = versions[-1]
        previous = versions[-2] if len(versions) >= 2 else None
        return previous, latest

    @staticmethod
    def _mandatory_calls(query: str, tools: list[Any], registry: KnowledgeToolRegistry | None = None) -> list[dict[str, Any]]:
        """Guarantee authoritative Live Jira/knowledge/current-evidence routing."""
        text = str(query or "")
        lowered = text.lower()
        available = {tool.name for tool in tools}
        calls: list[dict[str, Any]] = []

        # Deterministic routing for the developer-facing Engineering Assistant.
        engineering_assistant_markers = (
            "engineering assistant", "developer assistant", "review my current changes",
            "review current changes", "developer brief", "engineering brief",
            "what should i do next", "what should we do next",
            "developer test plan", "change review and test plan",
            "my current change", "current change", "current changes",
            "what should i regression test", "what should we regression test",
        )
        if any(marker in lowered for marker in engineering_assistant_markers) and "engineering_assistant" in available:
            calls.append({
                "name": "engineering_assistant",
                "args": {},
                "id": "mandatory_engineering_assistant",
            })

        # Deterministic routing for current-change regression/release decisions.
        release_intelligence_markers = (
            "release readiness", "ready for release", "release ready",
            "what should i retest", "what should we retest", "what to retest",
            "regression recommendation", "regression recommendations",
            "coverage gap", "coverage gaps", "release review",
        )
        if (
            not any(call.get("name") == "engineering_assistant" for call in calls)
            and any(marker in lowered for marker in release_intelligence_markers)
            and "release_regression_intelligence" in available
        ):
            calls.append({
                "name": "release_regression_intelligence",
                "args": {},
                "id": "mandatory_release_regression_intelligence",
            })

        # Exact Java symbols must be retrieved before synthesis, never left to the LLM.
        java_symbol = re.search(r"\b[A-Z][A-Za-z0-9_]*(?:Service|Controller|Mapper|Repository|Request|Entity|Model|Dto|DTO)\b", text)
        java_intent = any(x in lowered for x in (
            "method", "signature", "execution flow", "implementation", "source code",
            "java code", "codebase", "class", "trace", "retrieve", "search", "created", "purpose", "why"))
        if java_symbol and java_intent and "java_source_search" in available:
            calls.append({
                "name": "java_source_search",
                "args": {"query": text, "top_k": 10},
                "id": "mandatory_phase8_java_source_search",
            })

        # Class-origin questions require requirements and graph evidence, not just Git diff.
        class_origin = bool(java_symbol) and any(phrase in lowered for phrase in (
            "why was", "why is", "why does", "created", "introduced", "purpose",
            "which jira", "what jira", "which requirement", "what requirement",
        ))
        if class_origin:
            if "unified_knowledge_search" in available:
                calls.append({"name": "unified_knowledge_search", "args": {"query": text, "top_k": 10},
                              "id": "mandatory_class_origin_knowledge"})
            if "graph_search" in available:
                calls.append({"name": "graph_search", "args": {"entity": java_symbol.group(0)},
                              "id": "mandatory_class_origin_graph"})
            if "enterprise_hybrid_search" in available:
                calls.append({"name": "enterprise_hybrid_search", "args": {"query": text, "top_k": 10},
                              "id": "mandatory_class_origin_pgvector"})

        # Cross-source attribute questions must not stop at Java-only impact or Git diffs.
        # A dotted Java field (e.g. CustomerXmlRequest.countryCode) is resolved to
        # its business attribute for authoritative mapping lookup, while retrieval
        # separately discovers candidate JIRAs without claiming explicit linkage.
        attribute_impact_question = any(term in lowered for term in (
            "impact", "impacted", "affected", "if i change", "changing", "change to",
            "which jira", "which requirement", "mapping document", "where is", "mapped",
        ))
        field_match = re.search(r"\b[A-Z][A-Za-z0-9_]*\.([a-z][A-Za-z0-9_]*)\b", text)
        if field_match and attribute_impact_question:
            field = field_match.group(1)
            if "mapping_lineage" in available:
                calls.append({"name": "mapping_lineage", "args": {"attribute": field},
                              "id": "mandatory_attribute_mapping_lineage"})
            if "mapping_graph_lineage" in available:
                calls.append({"name": "mapping_graph_lineage", "args": {"attribute": field},
                              "id": "mandatory_attribute_mapping_graph"})
            if "enterprise_hybrid_search" in available:
                calls.append({"name": "enterprise_hybrid_search",
                              "args": {"query": f"{field} requirement JIRA mapping {text}",
                                       "top_k": 12, "source_types": ["JIRA", "REQUIREMENT", "MAPPING"]},
                              "id": "mandatory_attribute_jira_mapping_discovery"})
            if "scenario_impact" in available and any(x in lowered for x in ("scenario", "test", "impact", "affected")):
                calls.append({"name": "scenario_impact", "args": {"attribute": field},
                              "id": "mandatory_attribute_scenario_impact"})

        # Deterministic evidence retrieval for bare attributes and Jira coverage.
        # Do not depend on an LLM choosing the mapping/requirement tool.
        document_intent = any(x in lowered for x in (
            "mapping document", "mapping workbook", "which document defines",
            "which workbook", "mapping for", "mapping defines"))
        attributes = re.findall(r"\b[A-Z][A-Za-z0-9_]*\.([a-z][A-Za-z0-9_]*)\b", text)
        attributes += re.findall(r"\b(?:defines?|mapping for|attribute|field)\s+`?([a-z][A-Za-z0-9_]*)`?", text, flags=re.I)
        attributes += re.findall(r"`([a-z][A-Za-z0-9_]*)`", text)
        attributes += [x[0].lower() + x[1:] for x in re.findall(r"\b(?:xml element|element)\s+([A-Z][A-Za-z0-9_]*)\b", text, re.I)]
        ignored = {"the", "which", "mapping", "document", "workbook", "attribute", "field", "java", "xml", "from"}
        attributes = list(dict.fromkeys(a for a in attributes if len(a) > 2 and a.lower() not in ignored))[:3]
        for attr in attributes:
            if (document_intent or attribute_impact_question) and "mapping_lineage" in available:
                calls.append({"name": "mapping_lineage", "args": {"attribute": attr}, "id": f"evidence_mapping_{attr}"})
            if attribute_impact_question and "scenario_impact" in available and any(x in lowered for x in ("scenario", "test")):
                calls.append({"name": "scenario_impact", "args": {"attribute": attr}, "id": f"evidence_scenario_{attr}"})
        if attribute_impact_question and any(x in lowered for x in ("jira", "requirement", "mapping", "scenario")) and "enterprise_hybrid_search" in available:
            calls.append({"name": "enterprise_hybrid_search", "args": {"query": text, "top_k": 12, "source_types": ["JIRA", "REQUIREMENT", "MAPPING"]}, "id": "evidence_cross_source_requirements"})
        # Phase 8 deterministic routing for explicit enterprise/hybrid discovery.
        phase8_markers = (
            "enterprise hybrid", "hybrid rag", "enterprise rag",
            "search across all engineering knowledge",
            "search all engineering knowledge",
            "search across engineering knowledge",
        )
        if any(marker in lowered for marker in phase8_markers) and "enterprise_hybrid_search" in available:
            calls.append({
                "name": "enterprise_hybrid_search",
                "args": {"query": text, "top_k": 10},
                "id": "mandatory_phase8_enterprise_hybrid_search",
            })

        # Phase 7 deterministic routing: deeper source semantics must come from the
        # source parser, not from an LLM guess. Attribute extraction intentionally
        # supports common forms such as "gpa > 7", "flow of gpa" and "gpa condition".
        phase7_attr = None
        # Prefer intent-aware patterns before generic patterns.  This avoids taking
        # question words such as "What" from "What condition uses GPA?".
        p7_patterns = (
            r"\b(?:shared\s+components?|shared\s+mapper|common\s+components?|components?)\s+(?:are\s+)?(?:impacted|affected)\s+by\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:impacted|affected)\s+by\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:condition|branch|threshold|decision)\s+(?:uses?|reads?|checks?|references?)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\bwhere\s+(?:does|is|are)\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:flow|assigned|used|read|written|propagat(?:e|ed|ing))\b",
            r"\b(?:flow|data\s+flow|impact|condition|branch)\s+(?:of|for|about)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b(?:of|for|about)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:>=|<=|==|!=|>|<)",
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:condition|branch|flow|data\s+flow|impact)\b",
        )
        p7_match = None
        for pattern in p7_patterns:
            p7_match = re.search(pattern, text, flags=re.IGNORECASE)
            if p7_match:
                break
        if p7_match:
            candidate = p7_match.group(1).strip()
            p7_stop_words = {
                "what", "which", "where", "when", "why", "who", "how",
                "the", "a", "an", "code", "source", "current", "shared",
                "branch", "condition", "threshold", "decision", "data", "flow",
                "impact", "component", "components", "method", "methods",
            }
            if candidate.lower() not in p7_stop_words:
                phase7_attr = candidate

        p7_control = any(x in lowered for x in ("condition", "branch", "threshold", "decision", "control flow", "control-flow")) or bool(re.search(r"(?:>=|<=|==|!=|>|<)", text))
        p7_data = any(x in lowered for x in ("data flow", "data-flow", "propagat", "assignment", "assigned", "transformation", "where does", "where is"))
        p7_shared = any(x in lowered for x in ("shared component", "shared mapper", "common component", "ripple effect", "reused mapper"))
        p7_all = any(x in lowered for x in ("deep code", "deeper code", "complete code flow", "everything about the code flow", "full code flow"))

        if phase7_attr and p7_all and "deep_code_intelligence" in available:
            calls.append({"name": "deep_code_intelligence", "args": {"attribute": phase7_attr}, "id": "mandatory_phase7_deep_code"})
        elif phase7_attr and p7_control and "control_flow_analysis" in available:
            calls.append({"name": "control_flow_analysis", "args": {"attribute": phase7_attr}, "id": "mandatory_phase7_control_flow"})
        elif phase7_attr and p7_data and "data_flow_analysis" in available:
            calls.append({"name": "data_flow_analysis", "args": {"attribute": phase7_attr}, "id": "mandatory_phase7_data_flow"})
        elif phase7_attr and p7_shared and "shared_component_impact" in available:
            calls.append({"name": "shared_component_impact", "args": {"attribute": phase7_attr}, "id": "mandatory_phase7_shared_impact"})

        jira_ids = list(dict.fromkeys(
            re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", text.upper())
        ))

        if jira_ids and any(x in lowered for x in ("scenario", "test", "baseline", "coverage")):
            if "unified_knowledge_search" in available:
                calls.append({"name": "unified_knowledge_search", "args": {"query": f"{jira_ids[0]} scenario baseline test coverage"}, "id": "evidence_jira_scenario_registry"})
            if "graph_search" in available:
                calls.append({"name": "graph_search", "args": {"entity": jira_ids[0]}, "id": "evidence_jira_scenario_graph"})

        # A user explicitly asking for LIVE Jira must always retrieve the actual issue.
        live_jira_requested = (
            "live jira" in lowered
            or "current jira" in lowered
            or "jira live" in lowered
        )
        jira_analysis_requested = bool(jira_ids) and any(
            marker in lowered for marker in (
                "analy", "impact", "affected", "trace", "implementation",
                "java", "source code", "persistence", "endpoint", "mapping"
            )
        )
        if (live_jira_requested or jira_analysis_requested) and jira_ids and "live_jira_issue" in available:
            for index, jira_id in enumerate(jira_ids[:3], start=1):
                calls.append({
                    "name": "live_jira_issue",
                    "args": {"issue_key": jira_id},
                    "id": f"mandatory_live_jira_{index}",
                })

        cross_source_markers = (
            "all available engineering knowledge",
            "documented knowledge",
            "engineering knowledge",
            "architecture",
            "release",
            "api endpoint",
            "api endpoints",
            "schema",
            "test evidence",
            "test report",
        )
        cross_source = (
            "all available" in lowered
            or "cross-source" in lowered
            or sum(marker in lowered for marker in cross_source_markers) >= 2
        )
        if cross_source and "unified_knowledge_search" in available:
            calls.append({
                "name": "unified_knowledge_search",
                "args": {"query": text},
                "id": "mandatory_unified_knowledge",
            })
        if cross_source and jira_ids and "graph_search" in available:
            for index, jira_id in enumerate(jira_ids[:3], start=1):
                calls.append({
                    "name": "graph_search",
                    "args": {"entity": jira_id},
                    "id": f"mandatory_graph_{index}",
                })

        # Phase 5: relationship-aware questions about a concrete engineering entity
        # must include Neo4j evidence.  The LLM can still choose additional tools,
        # but it cannot silently answer an impact/history question only from the
        # relational/static services and skip the Engineering Knowledge Graph.
        graph_intent = any(marker in lowered for marker in (
            "impact", "relationship", "relationships", "connected", "dependency",
            "dependencies", "upstream", "downstream", "trace", "flow",
            "baseline history", "release history", "what uses", "what is affected",
            "affected by", "history for", "history of",
        ))
        if graph_intent and "graph_search" in available and not (cross_source and jira_ids):
            entity = KnowledgeAgentService._extract_graph_entity(text)
            if entity:
                calls.append({
                    "name": "graph_search",
                    "args": {"entity": entity},
                    "id": "mandatory_phase5_graph",
                })

        # Mapping document discovery is different from mapping-row lookup.
        if any(term in lowered for term in (
            "mapping document", "mapping workbook", "original mapping", "mapping file",
            "download mapping", "open mapping", "show mappings for",
        )) and "mapping_document_catalog" in available:
            calls.append({"name": "mapping_document_catalog", "args": {},
                          "id": "mandatory_mapping_document_catalog"})

        # A workbook filename is a document identity, never an XML/JSON source path.
        workbook_match = re.search(r"([A-Za-z0-9_ .-]+\\.xls(?:x|m))", text, flags=re.I)
        workbook_filename = workbook_match.group(1).strip() if workbook_match else None
        if workbook_filename and "mapping_document_rows" in available:
            calls.append({
                "name": "mapping_document_rows",
                "args": {"filename": workbook_filename},
                "id": "mandatory_mapping_document_rows",
            })

        # Phase 6I.1/6I.2: deterministic natural-language source-side resolution.
        # The source path is evidence, not an attribute guess. This supports XML,
        # JSON and DB inputs such as /Student/GPA, customer.gpa and STUDENT.TEMP_LOCATION.
        source_path = None
        source_patterns = (
            r"`([^`]+)`",
            r"(/[A-Za-z0-9_./\-\[\]@]+)",
            r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_.]*)\b",
        )
        source_stop = {
            "student.mapping", "mapping.history", "mapping.compare",
        }
        for pattern in source_patterns:
            for match in re.findall(pattern, text):
                candidate = str(match or "").strip().rstrip(".,?!:;")
                lower_candidate = candidate.lower()
                looks_like_source = (
                    candidate.startswith("/")
                    or ("." in candidate and not lower_candidate.endswith((".xlsx", ".xlsm")))
                    or lower_candidate.startswith(("xpath:", "json:", "jsonpath:", "db:", "table:", "column:"))
                )
                if looks_like_source and lower_candidate not in source_stop:
                    source_path = candidate
                    break
            if source_path:
                break

        source_question = source_path is not None and any(marker in lowered for marker in (
            "map", "mapping", "mapped", "where does", "comes from", "come from",
            "which excel", "which document", "defined", "uses", "use ", "impact",
            "impacted", "affected", "downstream", "engineering components", "lineage"
        ))
        source_impact = source_question and any(marker in lowered for marker in (
            "impact", "impacted", "affected", "downstream", "engineering components",
            "what could break", "what breaks", "uses", "use "
        ))
        if source_question and not workbook_filename and "mapping_source_lineage" in available:
            calls.append({
                "name": "mapping_source_lineage",
                "args": {
                    "source_path": source_path,
                    "include_engineering_impact": source_impact,
                },
                "id": "mandatory_phase6_source_lineage",
            })

        # Phase 6I.3: mapping-family + relative-version resolution.
        # "What changed in the latest Student Mapping?" must compare the previous
        # family version with the latest one; it must NOT become attribute=Student.
        explicit_mapping_family = KnowledgeAgentService._extract_mapping_family(text)
        relative_latest = any(word in lowered for word in ("latest", "current", "newest"))
        relative_previous = any(word in lowered for word in ("previous", "prior"))
        relative_change = any(word in lowered for word in ("changed", "change", "changes", "compare", "difference"))
        relative_family_compare = bool(explicit_mapping_family and relative_latest and relative_change)

        if relative_family_compare and "mapping_compare" in available:
            previous_version, latest_version = KnowledgeAgentService._relative_mapping_versions(
                explicit_mapping_family, tools
            )
            if previous_version and latest_version:
                calls.append({
                    "name": "mapping_compare",
                    "args": {
                        "mapping_family": explicit_mapping_family,
                        "from_version": previous_version,
                        "to_version": latest_version,
                    },
                    "id": "mandatory_phase6_relative_mapping_compare",
                })

        # Phase 6I.3: deterministic relative-version validation.
        # Never allow the LLM to guess internal document IDs for questions such as
        # "Is the latest GPA mapping valid in code?". Resolve family/version/document
        # from authoritative mapping metadata first, then call mapping_validation.
        relative_validation_intent = (
            any(marker in lowered for marker in (
                "valid in code", "valid", "validate", "validation",
                "exist in code", "exists in code", "implemented in code",
            ))
            and (relative_latest or relative_previous)
            and "mapping" in lowered
        )

        relative_validation_attr = None
        if relative_validation_intent:
            match = re.search(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s+mapping\b",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                candidate = match.group(1).strip()
                if candidate.lower() not in {
                    "student", "customer", "latest", "current", "newest",
                    "previous", "prior", "the", "a", "an",
                }:
                    relative_validation_attr = candidate

        relative_validation_context = None
        if relative_validation_intent and registry:
            requested_relative = "previous" if relative_previous and not relative_latest else "latest"
            try:
                # First honor a real explicit family such as "Student Mapping".
                # Natural language such as "latest GPA mapping" is syntactically
                # extracted as "GPA mapping", but that may be an attribute phrase,
                # not a stored mapping family. If the family lookup does not resolve,
                # reinterpret the prefix (GPA) as the attribute and resolve the
                # strongest authoritative version lineage instead of letting the LLM
                # guess a document ID.
                if explicit_mapping_family:
                    relative_validation_context = registry.resolve_relative_mapping_context(
                        attribute=None,
                        relative_version=requested_relative,
                        mapping_family=explicit_mapping_family,
                    )

                if not relative_validation_context or not relative_validation_context.get("document_id"):
                    fallback_attr = relative_validation_attr
                    if not fallback_attr and explicit_mapping_family.lower().endswith(" mapping"):
                        fallback_attr = explicit_mapping_family[:-len(" Mapping")].strip()
                    if fallback_attr:
                        relative_validation_attr = fallback_attr
                        relative_validation_context = registry.resolve_relative_mapping_context(
                            attribute=fallback_attr,
                            relative_version=requested_relative,
                            mapping_family=None,
                        )
            except Exception:
                relative_validation_context = None

        if (
            relative_validation_intent
            and relative_validation_context
            and relative_validation_context.get("document_id")
            and "mapping_validation" in available
        ):
            calls.append({
                "name": "mapping_validation",
                "args": {"document_id": relative_validation_context["document_id"]},
                "id": "mandatory_phase6_relative_mapping_validation",
            })

        # Phase 6 completion: deterministic blast-radius, quality and consolidated intelligence.
        advanced_mapping_attr = relative_validation_attr
        if not advanced_mapping_attr:
            for pattern in (
                r"\b(?:latest|current|newest|previous|prior)\s+([A-Za-z_][A-Za-z0-9_]*)\s+mapping\b",
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s+mapping\b",
                r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            ):
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    candidate = match.group(1).rstrip(".,?!:;")
                    if candidate.lower() not in {"student", "customer", "the", "a", "an", "mapping"}:
                        advanced_mapping_attr = candidate
                        break

        mapping_change_impact_intent = (
            "mapping" in lowered
            and "change" in lowered
            and any(word in lowered for word in ("impact", "blast radius", "affected", "downstream"))
        )
        mapping_quality_intent = (
            "mapping" in lowered
            and any(word in lowered for word in ("conflict", "quality", "inconsistent", "consistency", "duplicate"))
        )
        mapping_consolidated_intent = (
            "mapping" in lowered
            and any(phrase in lowered for phrase in (
                "everything about", "complete mapping", "full mapping", "mapping intelligence",
                "mapping and its impact", "mapping with impact",
            ))
        )
        phase6_advanced_intent = mapping_change_impact_intent or mapping_quality_intent or mapping_consolidated_intent

        if mapping_change_impact_intent and advanced_mapping_attr and registry and "mapping_change_impact" in available:
            try:
                latest_ctx = registry.resolve_relative_mapping_context(attribute=advanced_mapping_attr, relative_version="latest")
                previous_ctx = registry.resolve_relative_mapping_context(
                    attribute=advanced_mapping_attr,
                    relative_version="previous",
                    mapping_family=latest_ctx.get("mapping_family"),
                ) if latest_ctx.get("mapping_family") else None
            except Exception:
                latest_ctx, previous_ctx = None, None
            if latest_ctx and previous_ctx and latest_ctx.get("mapping_family") and latest_ctx.get("document_version") and previous_ctx.get("document_version"):
                calls.append({
                    "name": "mapping_change_impact",
                    "args": {
                        "mapping_family": latest_ctx["mapping_family"],
                        "from_version": previous_ctx["document_version"],
                        "to_version": latest_ctx["document_version"],
                        "attribute": advanced_mapping_attr,
                    },
                    "id": "mandatory_phase6_mapping_change_impact",
                })

        if mapping_quality_intent and "mapping_quality" in available:
            calls.append({
                "name": "mapping_quality",
                "args": {"attribute": advanced_mapping_attr} if advanced_mapping_attr else {},
                "id": "mandatory_phase6_mapping_quality",
            })

        if mapping_consolidated_intent and advanced_mapping_attr and "mapping_intelligence" in available:
            calls.append({
                "name": "mapping_intelligence",
                "args": {"attribute": advanced_mapping_attr, "relative_version": "latest"},
                "id": "mandatory_phase6_mapping_intelligence",
            })

        # Phase 6H.1: deterministically orchestrate compound mapping questions.
        # Resolve family/version/document IDs from the DB instead of trusting the
        # model to reproduce exact stored metadata names.
        mapping_versions = re.findall(r"\bV\d+(?:\.\d+)*\b", text, flags=re.IGNORECASE)
        mapping_versions = list(dict.fromkeys(v.upper() for v in mapping_versions))
        mapping_attr = None
        attr_patterns = (
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s+mapping\b",
            r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        )
        for pattern in attr_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).rstrip(".,?!:;")
                if candidate.lower() not in {"student", "the", "a", "an", "excel", "xml", "java", "jira", "database", "customer", "account", "service", "project", "code", "all", "six", "mapping", "mappings", "implementation"}:
                    mapping_attr = candidate
                    break

        mapping_intent = any(marker in lowered for marker in (
            "mapping", "mapped from", "mapped to", "input path", "source path",
            "xml node", "xpath", "json node", "json path", "db column",
            "null handling", "null rule", "which mapping document", "data lineage"
        ))
        compare_intent = mapping_intent and len(mapping_versions) >= 2 and any(
            marker in lowered for marker in ("changed", "change", "compare", "between", "from")
        )
        validation_intent = mapping_intent and any(
            marker in lowered for marker in ("valid in code", "valid", "exist in code", "exists in code", "implemented in code")
        )
        mapping_impact_intent = mapping_intent and any(
            marker in lowered for marker in ("impact", "impacted", "engineering components", "downstream", "affected")
        )

        resolved_context = None
        if registry and mapping_attr and mapping_versions:
            try:
                resolved_context = registry.resolve_mapping_context(
                    attribute=mapping_attr,
                    from_version=mapping_versions[0] if len(mapping_versions) > 1 else None,
                    to_version=mapping_versions[1] if len(mapping_versions) > 1 else mapping_versions[0],
                )
            except Exception:
                resolved_context = None

        if compare_intent and registry and resolved_context and resolved_context.get("mapping_family") and "mapping_compare" in available:
            calls.append({
                "name": "mapping_compare",
                "args": {
                    "mapping_family": resolved_context["mapping_family"],
                    "from_version": mapping_versions[0],
                    "to_version": mapping_versions[1],
                    "attribute": mapping_attr,
                },
                "id": "mandatory_phase6_mapping_compare",
            })

        if validation_intent and resolved_context and resolved_context.get("to_document_id") and "mapping_validation" in available:
            calls.append({
                "name": "mapping_validation",
                "args": {"document_id": resolved_context["to_document_id"]},
                "id": "mandatory_phase6_mapping_validation",
            })

        # CAS-style cross-source requests name a set of business attributes rather
        # than a single attribute. Never infer an attribute from "Excel mapping".
        # Resolve authoritative mapping rows separately from Neo4j graph edges:
        # the former can exist even when the Jira-to-code edge is not recorded.
        customer_six_requested = (
            bool(jira_ids)
            and "customer" in lowered
            and bool(re.search(r"\b(?:all\s+)?six\b|\b6\s+customer\b", lowered))
            and mapping_intent
        )
        if customer_six_requested:
            for attr in ("customerId", "firstName", "lastName", "email", "countryCode", "accountType"):
                if "mapping_lineage" in available:
                    calls.append({
                        "name": "mapping_lineage", "args": {"attribute": attr},
                        "id": f"mandatory_customer_mapping_{attr}",
                    })
                if "mapping_graph_lineage" in available:
                    calls.append({
                        "name": "mapping_graph_lineage", "args": {"attribute": attr},
                        "id": f"mandatory_customer_graph_mapping_{attr}",
                    })

        if mapping_impact_intent and mapping_attr and not customer_six_requested and "mapping_graph_lineage" in available:
            calls.append({
                "name": "mapping_graph_lineage",
                "args": {"attribute": mapping_attr},
                "id": "mandatory_phase6_mapping_graph",
            })

        # Phase 6: mapping/lineage questions must include authoritative mapping-row evidence.
        mapping_intent = any(marker in lowered for marker in (
            "mapping", "mapped from", "mapped to", "input path", "source path",
            "xml node", "xpath", "json node", "json path", "db column",
            "null handling", "null rule", "which mapping document", "data lineage"
        ))
        if mapping_intent and not jira_ids and not workbook_filename and not compare_intent and not relative_family_compare and not relative_validation_intent and not source_question and not phase6_advanced_intent and "mapping_lineage" in available:
            entity = KnowledgeAgentService._extract_graph_entity(text)
            if entity:
                calls.append({
                    "name": "mapping_lineage",
                    "args": {"attribute": entity},
                    "id": "mandatory_phase6_mapping",
                })

        # Test-evidence questions must not be answered from baseline history alone.
        test_evidence_requested = any(marker in lowered for marker in (
            "which tests", "what tests", "test evidence", "tests provide evidence",
            "tests cover", "test covers", "tests validate", "test validates",
            "tested by", "junit evidence", "runtime test"
        ))
        if test_evidence_requested and "attribute_test_evidence" in available:
            # Prefer explicit camelCase/snake_case/business identifiers following common
            # test-evidence phrases. Fall back to the last identifier-like token.
            patterns = (
                r"(?:for|cover|covers|validate|validates)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
                r"evidence\s+for\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
            )
            attribute = None
            for pattern in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    attribute = match.group(1)
                    break
            if attribute:
                calls.append({
                    "name": "attribute_test_evidence",
                    "args": {"attribute": attribute},
                    "id": "mandatory_attribute_test_evidence",
                })

        # Keep current Git evidence separate when the user explicitly asks to compare
        # a live Jira against current source/current implementation.
        current_evidence_requested = any(marker in lowered for marker in (
            "current source",
            "current source code",
            "current code",
            "implemented",
            "implementation",
            "git",
            "current changes",
            "uncommitted",
            "staged",
        ))
        if current_evidence_requested and "git_sdlc_traceability" in available:
            calls.append({
                "name": "git_sdlc_traceability",
                "args": {"jira_id": jira_ids[0]} if live_jira_requested and jira_ids else {},
                "id": "mandatory_current_git_sdlc",
            })

        return calls

    @staticmethod
    def _message_text(message: AIMessage) -> str:
        content = message.content
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()
        return str(content or "").strip()

    @staticmethod
    def _build_model():
        provider = (settings.LLM_PROVIDER or "ollama").strip().lower()

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=settings.OLLAMA_MODEL,
                base_url=settings.OLLAMA_BASE_URL,
                temperature=0,
            )

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            if not (settings.OPENAI_API_KEY or "").strip():
                raise RuntimeError("OPENAI_API_KEY is not configured")
            return ChatOpenAI(
                model=settings.OPENAI_MODEL,
                api_key=settings.OPENAI_API_KEY,
                temperature=0,
            )

        if provider in {"azure", "azure_openai", "azure_foundry"}:
            from langchain_openai import AzureChatOpenAI

            endpoint = str(os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip()
            deployment = str(os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
            api_version = str(os.getenv("AZURE_OPENAI_API_VERSION") or "2024-12-01-preview").strip()
            api_key = str(os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
            token = str(os.getenv("AZURE_OPENAI_TOKEN") or "").strip()
            token_provider = None

            if not endpoint or not deployment:
                raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are required")

            if not api_key and not token:
                try:
                    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                    scope = str(
                        os.getenv("AZURE_OPENAI_TOKEN_SCOPE")
                        or "https://cognitiveservices.azure.com/.default"
                    ).strip()
                    token_provider = get_bearer_token_provider(DefaultAzureCredential(), scope)
                except Exception as exc:
                    raise RuntimeError(
                        "Azure credentials are not configured. Set AZURE_OPENAI_API_KEY / "
                        "AZURE_OPENAI_TOKEN or configure DefaultAzureCredential."
                    ) from exc

            kwargs = {
                "azure_endpoint": endpoint,
                "azure_deployment": deployment,
                "api_version": api_version,
                "temperature": 0,
            }
            if api_key:
                kwargs["api_key"] = api_key
            elif token:
                kwargs["azure_ad_token"] = token
            else:
                kwargs["azure_ad_token_provider"] = token_provider
            return AzureChatOpenAI(**kwargs)

        raise RuntimeError(
            f"Unsupported LLM_PROVIDER '{provider}'. Use ollama, openai, azure_openai or azure_foundry."
        )
