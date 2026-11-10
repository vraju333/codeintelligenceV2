"""Regression checks for unsupported ASK call-graph assertions."""
from services.agent.evidence_integrity import assess, guard_answer


def test_lexical_source_does_not_verify_execution_flow():
    import json
    results = [{"name": "java_source_search", "content": json.dumps({
        "exact_matches": [{"file_path": "src/test/Example.java", "line_number": 12,
                           "source": "import example.JobLauncher;", "sha256": "abc",
                           "evidence": "CURRENT_JAVA_SOURCE"}]
    })}]
    query = "Explain the execution flow and verified caller relationships"
    evidence = assess(query, results)
    response = guard_answer(query, "Verified Call Paths: JobLauncher -> Job -> Step", evidence)
    assert "execution flow not verified" in response
    assert "src/test/Example.java" in response
    assert "Verified Call Paths" not in response


def test_non_flow_question_keeps_normal_response():
    query = "Find references to JobLauncher"
    evidence = assess(query, [])
    assert guard_answer(query, "Found reference", evidence) == "Found reference"


def test_unrelated_rag_does_not_upgrade_lexical_matches_to_call_graph():
    import json
    results = [
        {"name": "java_source_search", "content": json.dumps({"exact_matches": [
            {"file_path": "src/main/Launcher.java", "line_number": 8, "source": "launcher.run(job);"} ]})},
        {"name": "rag_search", "content": json.dumps({"matches": [{"text": "JobLauncher"}]})},
    ]
    evidence = assess("Show the execution flow", results)
    assert evidence["source_search_only"] is True
    answer = guard_answer("Show the execution flow", "Verified caller-to-callee edges", evidence)
    assert "execution flow not verified" in answer
    assert "Verified caller-to-callee edges" not in answer
