from __future__ import annotations

import os
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
Prefer graph_search when the user supplies an exact known engineering entity and asks for relationships.
Prefer rag_search for semantic/document discovery questions.
Prefer unified_knowledge_search for complete cross-source impact or 'everything about' questions.
When a question explicitly asks for all available engineering knowledge, documented knowledge, requirements, releases, architecture, API/schema documentation, or ingested test evidence, unified_knowledge_search is mandatory.
When such a cross-source question also contains an exact JIRA ID, graph_search is also mandatory for relationship evidence.
Do not treat Git/SDLC, static analysis, or baseline evidence as a substitute for ingested knowledge documents.
Use static_code_analysis for current source-code impact of an attribute.
Use scenario_impact for affected scenario/operation questions.
Use baseline_history for previous testing, release or baseline-history questions.
Use git_change_analysis whenever the question refers to current Git changes, current/uncommitted/staged changes, what changed, or asks which requirements/JIRAs relate to current changes.
For Git-to-requirement/JIRA correlation, call git_change_analysis first. Then use the returned changed attributes/classes as evidence and call unified_knowledge_search for the relevant changed concepts or exact JIRA IDs. Do not infer current Git changes from RAG or graph_search alone.
For questions spanning current code, scenarios and historical testing, call multiple relevant tools and combine their evidence.
You may call another tool after observing a tool result if it is genuinely needed.
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
        model = self._build_model().bind_tools(tools)

        # Deterministic evidence routing for cross-source engineering-knowledge questions.
        # The LLM still chooses all other tools dynamically, but it cannot accidentally
        # skip the ingested knowledge layer when the user explicitly asks for it.
        mandatory_calls = self._mandatory_knowledge_calls(query, tools)
        mandatory_calls.extend(self._mandatory_live_evidence_calls(query, tools))

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

        def agent_node(state: MessagesState):
            response = model.invoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
            return {"messages": [response]}

        builder = StateGraph(MessagesState)
        builder.add_node("router", router_node)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "router")
        if mandatory_calls:
            builder.add_edge("router", "tools")
        else:
            builder.add_edge("router", "agent")
        builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
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
    def _mandatory_knowledge_calls(query: str, tools: list[Any]) -> list[dict[str, Any]]:
        """Guarantee knowledge retrieval only when the user explicitly asks cross-source knowledge."""
        lowered = str(query or "").lower()
        available = {tool.name for tool in tools}

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

        calls: list[dict[str, Any]] = []
        if cross_source and "unified_knowledge_search" in available:
            calls.append({
                "name": "unified_knowledge_search",
                "args": {"query": query},
                "id": "mandatory_unified_knowledge",
            })

        jira_ids = list(dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", query.upper())))
        if cross_source and jira_ids and "graph_search" in available:
            for index, jira_id in enumerate(jira_ids[:3], start=1):
                calls.append({
                    "name": "graph_search",
                    "args": {"entity": jira_id},
                    "id": f"mandatory_graph_{index}",
                })

        return calls


    @classmethod
    def _mandatory_live_evidence_calls(cls, query: str, tools: list[Any]) -> list[dict[str, Any]]:
        """Guarantee live evidence when a cross-source question explicitly asks for current reality."""
        lowered = str(query or "").lower()
        available = {tool.name for tool in tools}

        live_markers = (
            "current source",
            "current source-code",
            "current code",
            "current git",
            "git changes",
            "source-code impact",
            "source code impact",
            "traceability gaps",
            "all available engineering knowledge",
        )
        wants_live = any(marker in lowered for marker in live_markers)
        if not wants_live:
            return []

        calls: list[dict[str, Any]] = []

        # One evidence-only SDLC call covers the current Git working tree, confirmed
        # JIRAs, scenarios, JUnit evidence and baseline/release readiness.
        if "git_sdlc_traceability" in available:
            calls.append({
                "name": "git_sdlc_traceability",
                "args": {},
                "id": "mandatory_current_git_sdlc",
            })

        # Attribute-specific live evidence is intentionally limited to identifiers
        # explicitly named by the user. Never manufacture attributes from retrieved docs.
        attributes = cls._explicit_attribute_candidates(query)
        for index, attribute in enumerate(attributes[:6], start=1):
            if "static_code_analysis" in available:
                calls.append({
                    "name": "static_code_analysis",
                    "args": {"attribute": attribute},
                    "id": f"mandatory_static_{index}",
                })
            if "baseline_history" in available:
                calls.append({
                    "name": "baseline_history",
                    "args": {"attribute": attribute},
                    "id": f"mandatory_baseline_{index}",
                })

        return calls

    @staticmethod
    def _explicit_attribute_candidates(query: str) -> list[str]:
        """Extract likely attribute identifiers explicitly written in the question."""
        text = str(query or "")
        candidates: list[str] = []

        # Backticks/quotes are strong identifier signals.
        for value in re.findall(r"[`'\"]([A-Za-z_][A-Za-z0-9_]*)[`'\"]", text):
            candidates.append(value)

        # camelCase and snake_case are also strong source-attribute signals.
        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text):
            if "_" in token or (re.search(r"[a-z][A-Z]", token) is not None):
                candidates.append(token)

        # For natural-language forms such as "country and temporaryLocation changes",
        # inspect the short phrase immediately before change/changes.
        for match in re.finditer(
            r"(?:\b([A-Za-z][A-Za-z0-9_]*)\b(?:\s+and\s+|\s*,\s*)){0,3}"
            r"\b([A-Za-z][A-Za-z0-9_]*)\b\s+changes?\b",
            text,
            flags=re.IGNORECASE,
        ):
            candidates.extend(group for group in match.groups() if group)

        stop = {
            "all", "available", "engineering", "knowledge", "current", "source", "code",
            "sourcecode", "git", "requirement", "requirements", "release", "architecture",
            "component", "components", "api", "endpoint", "endpoints", "schema", "schemas",
            "executed", "test", "evidence", "traceability", "gap", "gaps", "documented",
            "impact", "change", "changes", "jira", "jiras", "related", "using", "show",
            "analyze", "analysis", "and", "the", "with", "from", "against",
        }
        jira_pattern = re.compile(r"^[A-Z][A-Z0-9]+-\d+$", re.IGNORECASE)

        result: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            value = candidate.strip()
            key = value.lower()
            if not value or key in stop or jira_pattern.match(value) or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

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
