"""Explainable review indicators, not a probability or release approval."""
from pathlib import PurePosixPath


def build_risk_report(source, endpoint_changed, db_changed, added, removed, tests,
                      scenario_code, dependencies=()):
    names = {str(name).split('.')[-1] for name in dependencies}
    candidates = [item for item in source.get("changed_files", [])
                  if PurePosixPath(item["file_path"]).stem in names]
    reasons, recommendations = [], []
    score = 0
    if candidates:
        score += 20
        reasons.append("20 points: changed files match stored scenario class dependencies.")
        recommendations.append(f"Rerun the saved tests for {scenario_code} and review the matched file diffs.")
    if endpoint_changed:
        score += 25
        reasons.append("25 points: the endpoint or HTTP method changed.")
        recommendations.append("Verify callers and the endpoint request/response contract.")
    if db_changed:
        score += 20
        reasons.append("20 points: the recorded database expectation changed.")
        recommendations.append("Verify the expected persisted values against the target version.")
    if added or removed:
        score += 15
        reasons.append("15 points: stored method membership changed.")
        recommendations.append("Review added/removed flow methods; membership does not prove runtime execution.")
    counts = {"PASS": 0, "FAIL": 0, "NOT_RUN": 0, "UNKNOWN": 0}
    evidence = []
    for test in tests:
        status = str(getattr(test, "status", "UNKNOWN") or "UNKNOWN").upper()
        status = status if status in counts else "UNKNOWN"
        counts[status] += 1
        evidence.append({"name": test.baseline_name, "status": status,
                         "jira_ids": list(test.jira_ids or [])})
    if counts["FAIL"]:
        score += 40
        reasons.append("40 points: the target version has recorded failing tests.")
        recommendations.append("Investigate failed test evidence before release review.")
    if not evidence or counts["NOT_RUN"] or counts["UNKNOWN"]:
        score += 20
        reasons.append("20 points: target-version test evidence is missing, not run, or unknown.")
        recommendations.append("Record completed test results for the selected target version.")
    uncertain = source.get("snapshot_status") != "AVAILABLE" or not names
    if uncertain:
        recommendations.append("Resolve incomplete source/dependency evidence before judging release readiness.")
    score = min(score, 100)
    level = "HIGH" if score >= 60 else "MEDIUM" if score >= 25 else "LOW"
    return {
        "mode": "deterministic", "rules_version": "1", "llm_used": False,
        "scenario_code": scenario_code, "scope": "Selected scenario and baseline pair",
        "score": score, "risk_level": "UNKNOWN" if uncertain else level,
        "evidence_complete": not uncertain, "reasons": reasons,
        "candidate_files": candidates, "test_counts": counts, "tests": evidence,
        "recommendations": recommendations,
        "limitations": "Heuristic review indicator, not release approval. Class-name matches are potential impact, "
                      "not method-level proof. Stored PASS results do not mean this tool executed tests.",
    }
