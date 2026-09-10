from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from services.jira.jira_knowledge_service import jira_knowledge_service


class AttributeJiraState(TypedDict, total=False):
    query: str
    db: Session
    code_result: dict
    jira_results: list[dict]
    final_result: dict


class AttributeJiraGraph:
    """Runs code impact and local JIRA RAG as parallel LangGraph branches."""

    def __init__(self, code_search):
        self.code_search = code_search

    def run(self, query: str, db: Session) -> dict:
        graph = StateGraph(AttributeJiraState)

        def code_node(state: AttributeJiraState):
            return {"code_result": self.code_search(state["query"], state["db"])}

        def jira_node(state: AttributeJiraState):
            return {
                "jira_results": jira_knowledge_service.search(
                    state["db"], state["query"], top_k=8
                )
            }

        def merge_node(state: AttributeJiraState):
            result = dict(state.get("code_result") or {})
            scenarios = result.get("affected_scenarios") or []
            scenario_codes = {
                str(item.get("scenario_code") or "").lower()
                for item in scenarios
                if item.get("scenario_code")
            }
            impacted_classes = {
                str(item).lower()
                for item in result.get("impacted_classes") or []
            }
            attribute = str(result.get("attribute") or state["query"]).lower()

            related = []
            for jira in state.get("jira_results") or []:
                haystack = " ".join([
                    jira.get("title") or "",
                    jira.get("requirement") or "",
                ]).lower()

                reasons = []
                if attribute and attribute in haystack:
                    reasons.append(f"Requirement directly mentions attribute '{result.get('attribute') or state['query']}'")

                matched_classes = sorted(
                    original for original in result.get("impacted_classes") or []
                    if str(original).lower() in haystack
                )
                if matched_classes:
                    reasons.append("Requirement mentions impacted class/domain: " + ", ".join(matched_classes[:4]))

                matched_scenarios = sorted(
                    code for code in scenario_codes if code and code in haystack
                )
                if matched_scenarios:
                    reasons.append("Requirement mentions affected scenario: " + ", ".join(matched_scenarios[:3]))

                # RAG itself is useful evidence even when exact words differ.
                if not reasons:
                    reasons.append("Semantic match from local JIRA RAG")

                related.append({
                    **jira,
                    "relationship": "DIRECT_ATTRIBUTE_MATCH" if attribute in haystack else "SEMANTIC_RELATED",
                    "reasons": reasons,
                })

            result["related_jiras"] = related[:6]
            result["analysis_basis"] = {
                **(result.get("analysis_basis") or {}),
                "jira_rag_used": True,
                "orchestration": "LANGGRAPH_PARALLEL",
            }
            return {"final_result": result}

        graph.add_node("code_search", code_node)
        graph.add_node("jira_search", jira_node)
        graph.add_node("merge", merge_node)

        # Fan out from START: LangGraph schedules these independent branches in parallel.
        graph.add_edge(START, "code_search")
        graph.add_edge(START, "jira_search")
        graph.add_edge("code_search", "merge")
        graph.add_edge("jira_search", "merge")
        graph.add_edge("merge", END)

        app = graph.compile()
        state = app.invoke({"query": query, "db": db})
        return state["final_result"]
