from langgraph.graph import END, StateGraph

from graph.investigation_state import InvestigationState
from services.defect.defect_comparison_service import DefectComparisonService
from services.flow.endpoint_flow_service import EndpointFlowService
from services.flow.scenario_attribute_trace_service import ScenarioAttributeTraceService
from services.rag.rag_service import RagService


class InvestigationGraph:

    def __init__(self):
        self.defect_service = DefectComparisonService()
        self.endpoint_flow_service = EndpointFlowService()
        self.attribute_trace_service = ScenarioAttributeTraceService()
        self.rag_service = RagService()

        self.graph = self._build_graph()

    def _build_graph(self):

        builder = StateGraph(InvestigationState)

        builder.add_node(
            "compare",
            self._compare_node
        )

        builder.add_node(
            "rag_search",
            self._rag_search_node
        )

        builder.add_node(
            "endpoint_flow",
            self._endpoint_flow_node
        )

        builder.add_node(
            "attribute_trace",
            self._attribute_trace_node
        )

        builder.add_node(
            "build_result",
            self._build_result_node
        )

        builder.set_entry_point("compare")

        builder.add_conditional_edges(
            "compare",
            self._after_compare,
            {
                "PASS": END,
                "INVESTIGATE": "rag_search"
            }
        )

        builder.add_edge(
            "rag_search",
            "endpoint_flow"
        )

        builder.add_edge(
            "endpoint_flow",
            "attribute_trace"
        )

        builder.add_edge(
            "attribute_trace",
            "build_result"
        )

        builder.add_edge(
            "build_result",
            END
        )

        return builder.compile()

    def _compare_node(
        self,
        state: InvestigationState
    ) -> dict:

        result = self.defect_service.compare(
            http_method=state["http_method"],
            endpoint=state["endpoint"],
            expected=state["expected"],
            actual=state["actual"]
        )

        return {
            "differences": result.get(
                "differences",
                []
            ),
            "affected_attributes": result.get(
                "affected_attributes",
                []
            ),
            "status": result.get(
                "status",
                "UNKNOWN"
            )
        }

    def _after_compare(
        self,
        state: InvestigationState
    ) -> str:

        if not state.get("differences"):
            return "PASS"

        return "INVESTIGATE"

    def _rag_search_node(
        self,
        state: InvestigationState
    ) -> dict:

        rag_results = {}

        for attribute in state.get(
            "affected_attributes",
            []
        ):

            results = self.rag_service.search(
                query=attribute,
                top_k=5
            )

            rag_results[attribute] = results

        return {
            "rag_results": rag_results
        }

    def _endpoint_flow_node(
        self,
        state: InvestigationState
    ) -> dict:

        result = self.endpoint_flow_service.analyze_endpoint(
            http_method=state["http_method"],
            endpoint=state["endpoint"]
        )

        return {
            "endpoint_flow": result
        }

    def _attribute_trace_node(
        self,
        state: InvestigationState
    ) -> dict:

        traces = {}
        locations = {}

        for attribute in state.get(
            "affected_attributes",
            []
        ):
            trace = self.attribute_trace_service.trace(
                http_method=state["http_method"],
                endpoint=state["endpoint"],
                attribute_name=attribute,
                input_context=state.get("input")
            )
            traces[attribute] = trace

            locations[attribute] = (
                self._extract_likely_locations(
                    trace
                )
            )

        return {
            "attribute_traces": traces,
            "likely_code_locations": locations
        }

    def _extract_likely_locations(
        self,
        trace: dict
    ) -> list[dict]:

        locations = []

        for step in trace.get("trace", []):

            if not step.get(
                "direct_attribute_touch"
            ):
                continue

            class_name = step.get(
                "class_name"
            )

            method_name = step.get(
                "method_name"
            )

            for evidence in step.get(
                "evidence",
                []
            ):

                locations.append(
                    {
                        "class_name": class_name,
                        "method_name": method_name,
                        "line_number": evidence.get(
                            "line_number"
                        ),
                        "usage_type": evidence.get(
                            "usage_type"
                        ),
                        "code": evidence.get(
                            "code"
                        )
                    }
                )

        return locations

    def _build_result_node(
        self,
        state: InvestigationState
    ) -> dict:

        investigations = []

        for attribute in state.get(
            "affected_attributes",
            []
        ):

            attribute_differences = [
                difference
                for difference
                in state.get(
                    "differences",
                    []
                )
                if difference.get(
                    "attribute"
                ) == attribute
            ]

            investigations.append(
                {
                    "attribute": attribute,

                    "differences":
                        attribute_differences,

                    "rag_results":
                        state.get(
                            "rag_results",
                            {}
                        ).get(
                            attribute,
                            []
                        ),

                    "likely_code_locations":
                        state.get(
                            "likely_code_locations",
                            {}
                        ).get(
                            attribute,
                            []
                        ),

                    "trace":
                        state.get(
                            "attribute_traces",
                            {}
                        ).get(
                            attribute,
                            {}
                        )
                }
            )

        final_result = {
            "http_method":
                state["http_method"],

            "endpoint":
                state["endpoint"],

            "input":
                state.get("input"),

            "status":
                "INVESTIGATION_COMPLETE",

            "total_differences":
                len(
                    state.get(
                        "differences",
                        []
                    )
                ),

            "differences":
                state.get(
                    "differences",
                    []
                ),

            "affected_attributes":
                state.get(
                    "affected_attributes",
                    []
                ),

            "endpoint_flow":
                state.get(
                    "endpoint_flow",
                    {}
                ),

            "investigations":
                investigations
        }

        return {
            "status": "INVESTIGATION_COMPLETE",
            "final_result": final_result
        }

    def investigate(
        self,
        http_method: str,
        endpoint: str,
        input,
        expected,
        actual
    ) -> dict:

        initial_state: InvestigationState = {
            "http_method":
                http_method,

            "endpoint":
                endpoint,

            "input":
                input,

            "expected":
                expected,

            "actual":
                actual
        }

        result = self.graph.invoke(
            initial_state
        )

        if not result.get(
            "differences"
        ):

            return {
                "http_method":
                    http_method,

                "endpoint":
                    endpoint,

                "input":
                    input,

                "status":
                    "PASS",

                "total_differences":
                    0,

                "differences":
                    [],

                "affected_attributes":
                    []
            }

        return result.get(
            "final_result",
            result
        )