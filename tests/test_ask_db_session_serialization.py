"""Regression guard: shared SQLAlchemy Session must never be used by parallel ToolNode calls."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "services/agent/knowledge_agent_service.py"

def test_agent_syntax():
    ast.parse(SOURCE.read_text(encoding="utf-8"))

def test_all_tool_phases_are_serialized():
    source = SOURCE.read_text(encoding="utf-8")
    for name in ("mandatory_tools", "enrichment_tools", "agent_tools"):
        assert f'builder.add_node("{name}", sequential_tools_node)' in source
        assert f'builder.add_node("{name}", ToolNode(tools))' not in source

def test_serial_execution_handles_one_tool_call_at_a_time():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'for call in calls:' in source
    assert 'tool_calls=[call]' in source
    assert 'ToolNode(tools).invoke({"messages": [single_call]})' in source
