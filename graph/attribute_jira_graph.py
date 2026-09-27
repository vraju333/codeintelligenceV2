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
            attribute = str(result.get("attribute") or state["query"]).strip()

            def tokens(value: str) -> set[str]:
                text = __import__("re").sub(
                    r"([a-z0-9])([A-Z])", r"\\1 \\2", str(value or "")
                )
                text = text.replace("_", " ").replace("-", " ")
                return {
                    token.lower()
                    for token in __import__("re").findall(r"[A-Za-z0-9]+", text)
                    if len(token) >= 2
                }

            attribute_tokens = tokens(attribute)

            # A JIRA can also qualify through an already-grounded historical
            # test baseline. This preserves explicit baseline -> JIRA linkage.
            linked_jira_ids = {
                str(jira_id).strip().upper()
                for item in (result.get("historical_traceability") or [])
                for jira_id in (item.get("jira_ids") or [])
                if str(jira_id).strip()
            }

            related = []
            active_project = str(__import__("config").settings.JAVA_PROJECT_PATH or "").lower()

            for jira in state.get("jira_results") or []:
                jira_project = str(jira.get("project_path") or "").lower()
                if jira_project and active_project and jira_project != active_project:
                    continue

                jira_id = str(jira.get("jira_id") or "").strip().upper()
                haystack = " ".join([
                    jira.get("title") or "",
                    jira.get("requirement") or "",
                ])
                jira_tokens = tokens(haystack)
                direct_attribute_match = bool(
                    attribute_tokens and attribute_tokens.issubset(jira_tokens)
                )
                linked_test_match = bool(jira_id and jira_id in linked_jira_ids)

                # Semantic similarity by itself is search evidence, not impact
                # evidence. Do not show a JIRA here unless the attribute is
                # explicit or a qualifying test baseline links it.
                if not direct_attribute_match and not linked_test_match:
                    continue

                reasons = []
                relationship = "DIRECT_ATTRIBUTE_MATCH"
                if direct_attribute_match:
                    reasons.append(
                        f"Requirement directly mentions attribute '{attribute}'"
                    )
                elif linked_test_match:
                    relationship = "LINKED_TEST_BASELINE"
                    reasons.append(
                        "Linked through an attribute-grounded test baseline"
                    )

                related.append({
                    **jira,
                    "relationship": relationship,
                    "reasons": reasons,
                })

            related.sort(key=lambda item: (
                0 if item.get("relationship") == "DIRECT_ATTRIBUTE_MATCH" else 1,
                str(item.get("jira_id") or ""),
            ))
            result["related_jiras"] = related[:6]
            result["analysis_basis"] = {
                **(result.get("analysis_basis") or {}),
                "jira_rag_used": True,
                "jira_rag_role": "CANDIDATE_RETRIEVAL_ONLY",
                "jira_impact_requires_grounding": True,
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
