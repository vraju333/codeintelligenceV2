from services.agent.knowledge_agent_service import KnowledgeAgentService


class _Tool:
    def __init__(self, name):
        self.name = name


def test_release_readiness_routes_deterministically():
    tools = [_Tool("release_regression_intelligence"), _Tool("enterprise_hybrid_search")]
    calls = KnowledgeAgentService._mandatory_calls("Are the current changes ready for release?", tools)
    assert calls == [{
        "name": "release_regression_intelligence",
        "args": {},
        "id": "mandatory_release_regression_intelligence",
    }]


def test_coverage_gap_routes_deterministically():
    tools = [_Tool("release_regression_intelligence")]
    calls = KnowledgeAgentService._mandatory_calls("Show me the coverage gaps for this release", tools)
    assert calls[0]["name"] == "release_regression_intelligence"
    assert calls[0]["id"] == "mandatory_release_regression_intelligence"
