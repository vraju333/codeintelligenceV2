from services.engineering_assistant.engineering_assistant_service import EngineeringAssistantService


def test_next_actions_for_review_required():
    service = EngineeringAssistantService()
    result = {
        "boundary_regression_recommendations": [{"attribute": "gpa", "value": 6.5}],
        "coverage_gaps": [{"gap_type": "NO_CAPTURED_TEST_BASELINE"}],
        "impact_summary": {
            "requirement_code_mismatches": 1,
            "mapping_quality_issues": 1,
            "graph_sync_mismatches": 0,
            "failed_test_baselines": 0,
        },
        "release_readiness": {"status": "REVIEW_REQUIRED"},
    }
    actions = service._next_actions(result)
    assert any("boundary-value" in item for item in actions)
    assert any("capture a test baseline" in item for item in actions)
    assert any("authoritative requirement/Jira" in item for item in actions)
    assert any("mapping/contract" in item for item in actions)


def test_test_plan_preserves_static_test_evidence():
    service = EngineeringAssistantService()
    result = {
        "regression_recommendations": [{
            "scenario_code": "CREATE_DATA_API_PROMOTIONS_CHECK",
            "operation": "POST /api/promotions/check",
            "impact_status": "DIRECTLY_AFFECTED",
            "recommended_action": "Rerun affected regression tests",
            "automated_test_evidence": [{
                "test_class": "StudentPromotionControllerTest",
                "test_method": "shouldReturnEligibleWhenGpaGreaterThanSeven",
                "file": "src/test/StudentPromotionControllerTest.java",
                "has_assertion": True,
                "evidence_basis": "FLOW_METHOD_AND_ATTRIBUTE_MATCH",
            }],
        }],
        "boundary_regression_recommendations": [{"attribute": "gpa", "value": 6.5}],
    }
    plan = service._test_plan(result)
    assert plan["existing_test_evidence"][0]["test_class"] == "StudentPromotionControllerTest"
    assert plan["boundary_tests_to_add_or_verify"][0]["value"] == 6.5

class _Tool:
    def __init__(self, name):
        self.name = name


def test_engineering_assistant_routes_deterministically():
    from services.agent.knowledge_agent_service import KnowledgeAgentService

    tools = [_Tool("engineering_assistant"), _Tool("release_regression_intelligence")]
    calls = KnowledgeAgentService._mandatory_calls(
        "Engineering assistant: review my current changes and tell me what I should do next",
        tools,
    )
    assert calls[0] == {
        "name": "engineering_assistant",
        "args": {},
        "id": "mandatory_engineering_assistant",
    }
    assert not any(call["name"] == "release_regression_intelligence" for call in calls)
