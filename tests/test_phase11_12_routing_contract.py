"""Dependency-light checks for the Phase 11/12 regression guard."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_behavioral_intent_guard_exists():
    source = (ROOT / "routers" / "knowledge_agent_router.py").read_text()
    ast.parse(source)
    assert "behavioral_change = bool(re.search(" in source
    assert "unknown_field and not requested_attributes and not behavioral_change" in source
    assert "if attribute and not behavioral_change" in source

def test_presentation_distinguishes_display_scope_from_verification():
    source = (ROOT / "services" / "agent" / "ask_presentation_planner.py").read_text()
    ast.parse(source)
    assert source.count('"impact_verified": bool(evidence.get("execution_verified", False))') == 3
