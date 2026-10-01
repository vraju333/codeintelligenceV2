from __future__ import annotations

import os
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

        def agent_node(state: MessagesState):
            response = model.invoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
            return {"messages": [response]}

        builder = StateGraph(MessagesState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "agent")
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
