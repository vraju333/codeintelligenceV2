from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):

    http_method: str
    endpoint: str

    input: Any
    expected: Any
    actual: Any

    differences: list[dict]
    affected_attributes: list[str]

    rag_results: dict[str, list[dict]]

    endpoint_flow: dict

    attribute_traces: dict[str, dict]

    likely_code_locations: dict[str, list[dict]]

    # Phase D - deterministic value-flow analysis results
    value_flow_findings: dict[str, dict]

    status: str

    final_result: dict