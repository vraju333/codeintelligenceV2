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


def test_jira_change_correlation_uses_code_and_test_evidence():
    from types import SimpleNamespace
    from services.jira.jira_change_correlation_service import JiraChangeCorrelationService

    class Query:
        def order_by(self, *args):
            return self
        def all(self):
            return [
                SimpleNamespace(
                    jira_id="KAN-25",
                    title="Change student promotion GPA threshold to 6.5",
                    requirement="Student promotion is eligible when gpa > 6.5",
                    project_path=r"D:\\AIlearning\\student-employee-project",
                ),
                SimpleNamespace(
                    jira_id="KAN-4",
                    title="Original GPA promotion rule",
                    requirement="Student promotion is eligible when gpa > 7",
                    project_path=r"D:\\AIlearning\\student-employee-project",
                ),
            ]

    class DB:
        def query(self, _model):
            return Query()

    result = {
        "project_path": r"D:\\AIlearning\\student-employee-project",
        "change_summary": {
            "changed_attributes": ["gpa"],
            "changed_classes": ["StudentPromotionController"],
            "changed_methods": [{"method_name": "checkPromotion"}],
        },
        "behavioral_changes": [{
            "attribute": "gpa",
            "old_operator": ">",
            "old_threshold": 7,
            "new_operator": ">",
            "new_threshold": 6.5,
            "old_condition": "student.getGpa() > 7",
            "new_condition": "student.getGpa() > 6.5",
        }],
        "affected_scenarios": [{
            "scenario_code": "CREATE_DATA_API_PROMOTIONS_CHECK",
            "http_method": "POST",
            "endpoint": "/api/promotions/check",
        }],
        "captured_test_baseline_evidence": {
            "CREATE_DATA_API_PROMOTIONS_CHECK": [{"test_baseline_name": "GPA Promotion scenario"}]
        },
        "regression_recommendations": [{
            "automated_test_evidence": [{
                "test_class": "StudentPromotionControllerTest",
                "test_method": "shouldReturnEligibleWhenGpaGreaterThanSeven",
            }]
        }],
    }

    correlation = JiraChangeCorrelationService().correlate(DB(), result, historical_jira_ids={"KAN-4"})
    assert correlation["candidates"]
    assert correlation["candidates"][0]["jira_id"] == "KAN-25"
    assert correlation["candidates"][0]["classification"] == "LIKELY_CURRENT_JIRA"
    assert any(r["type"] == "NEW_BEHAVIOR_RULE_MATCH" for r in correlation["candidates"][0]["why_matched"])
    assert not any(c["jira_id"] == "KAN-4" for c in correlation["candidates"])
