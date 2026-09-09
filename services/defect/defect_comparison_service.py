from typing import Any

from services.flow.scenario_attribute_trace_service import (
    ScenarioAttributeTraceService
)


class DefectComparisonService:

    def __init__(self):

        self.trace_service = (
            ScenarioAttributeTraceService()
        )

    # =========================================================
    # PUBLIC
    # =========================================================

    def compare(
        self,
        http_method: str,
        endpoint: str,
        expected: Any,
        actual: Any
    ) -> dict:

        http_method = (
            http_method.upper().strip()
        )

        endpoint = (
            endpoint.strip()
        )

        differences = []

        self._compare_values(
            expected=expected,
            actual=actual,
            path="",
            differences=differences
        )

        investigations = []

        traced_attributes = set()

        for difference in differences:

            attribute = (
                self._extract_attribute(
                    difference[
                        "path"
                    ]
                )
            )

            difference[
                "attribute"
            ] = attribute

            if not attribute:
                continue

            if attribute in traced_attributes:
                continue

            traced_attributes.add(
                attribute
            )

            investigation = (
                self._investigate_attribute(
                    http_method=http_method,
                    endpoint=endpoint,
                    attribute=attribute,
                    related_differences=[
                        item
                        for item in differences
                        if (
                            self._extract_attribute(
                                item["path"]
                            )
                            == attribute
                        )
                    ]
                )
            )

            investigations.append(
                investigation
            )

        return {
            "http_method":
                http_method,

            "endpoint":
                endpoint,

            "status":
                (
                    "MATCH"
                    if not differences
                    else "DIFFERENCES_FOUND"
                ),

            "total_differences":
                len(differences),

            "differences":
                differences,

            "affected_attributes":
                sorted(
                    traced_attributes
                ),

            "investigations":
                investigations
        }

    # =========================================================
    # RECURSIVE JSON COMPARISON
    # =========================================================

    def _compare_values(
        self,
        expected: Any,
        actual: Any,
        path: str,
        differences: list[dict]
    ):

        # If the expected response is an object/list but the actual response is
        # completely null/missing, report the individual expected leaf
        # attributes as missing.  A single root "$" mismatch is not useful for
        # defect tracing because it produces no affected attribute.
        if actual is None and isinstance(expected, (dict, list)):
            self._expand_missing_expected(
                expected=expected,
                path=path,
                differences=differences
            )
            return

        if (
            isinstance(expected, dict)
            and isinstance(actual, dict)
        ):

            self._compare_objects(
                expected=expected,
                actual=actual,
                path=path,
                differences=differences
            )

            return

        if (
            isinstance(expected, list)
            and isinstance(actual, list)
        ):

            self._compare_lists(
                expected=expected,
                actual=actual,
                path=path,
                differences=differences
            )

            return

        if (
            type(expected)
            is not type(actual)
            and expected is not None
            and actual is not None
        ):

            differences.append(
                {
                    "path":
                        self._display_path(
                            path
                        ),

                    "difference_type":
                        "TYPE_MISMATCH",

                    "expected":
                        expected,

                    "actual":
                        actual,

                    "expected_type":
                        type(
                            expected
                        ).__name__,

                    "actual_type":
                        type(
                            actual
                        ).__name__
                }
            )

            return

        if expected != actual:

            differences.append(
                {
                    "path":
                        self._display_path(
                            path
                        ),

                    "difference_type":
                        "VALUE_MISMATCH",

                    "expected":
                        expected,

                    "actual":
                        actual
                }
            )

    def _expand_missing_expected(
        self,
        expected: Any,
        path: str,
        differences: list[dict]
    ):
        if isinstance(expected, dict):
            for key, value in expected.items():
                child_path = (
                    f"{path}.{key}"
                    if path
                    else str(key)
                )
                self._expand_missing_expected(
                    expected=value,
                    path=child_path,
                    differences=differences
                )
            return

        if isinstance(expected, list):
            for index, value in enumerate(expected):
                child_path = (
                    f"{path}[{index}]"
                    if path
                    else f"[{index}]"
                )
                self._expand_missing_expected(
                    expected=value,
                    path=child_path,
                    differences=differences
                )
            return

        differences.append(
            {
                "path": self._display_path(path),
                "difference_type": "MISSING_VALUE",
                "expected": expected,
                "actual": None
            }
        )


    # =========================================================
    # OBJECT COMPARISON
    # =========================================================

    def _compare_objects(
        self,
        expected: dict,
        actual: dict,
        path: str,
        differences: list[dict]
    ):

        expected_keys = set(
            expected.keys()
        )

        actual_keys = set(
            actual.keys()
        )

        all_keys = (
            expected_keys
            | actual_keys
        )

        for key in sorted(
            all_keys
        ):

            current_path = (
                self._join_path(
                    path,
                    str(key)
                )
            )

            if key not in actual:

                differences.append(
                    {
                        "path":
                            current_path,

                        "difference_type":
                            "MISSING_IN_ACTUAL",

                        "expected":
                            expected[
                                key
                            ],

                        "actual":
                            None
                    }
                )

                continue

            if key not in expected:

                differences.append(
                    {
                        "path":
                            current_path,

                        "difference_type":
                            "UNEXPECTED_IN_ACTUAL",

                        "expected":
                            None,

                        "actual":
                            actual[
                                key
                            ]
                    }
                )

                continue

            self._compare_values(
                expected=expected[
                    key
                ],
                actual=actual[
                    key
                ],
                path=current_path,
                differences=differences
            )

    # =========================================================
    # LIST COMPARISON
    # =========================================================

    def _compare_lists(
        self,
        expected: list,
        actual: list,
        path: str,
        differences: list[dict]
    ):

        common_length = min(
            len(expected),
            len(actual)
        )

        for index in range(
            common_length
        ):

            current_path = (
                f"{path}[{index}]"
                if path
                else f"[{index}]"
            )

            self._compare_values(
                expected=expected[
                    index
                ],
                actual=actual[
                    index
                ],
                path=current_path,
                differences=differences
            )

        if (
            len(expected)
            != len(actual)
        ):

            differences.append(
                {
                    "path":
                        self._display_path(
                            path
                        ),

                    "difference_type":
                        "LIST_LENGTH_MISMATCH",

                    "expected":
                        len(expected),

                    "actual":
                        len(actual),

                    "expected_length":
                        len(expected),

                    "actual_length":
                        len(actual)
                }
            )

        if len(expected) > common_length:

            for index in range(
                common_length,
                len(expected)
            ):

                current_path = (
                    f"{path}[{index}]"
                    if path
                    else f"[{index}]"
                )

                differences.append(
                    {
                        "path":
                            current_path,

                        "difference_type":
                            "MISSING_IN_ACTUAL",

                        "expected":
                            expected[
                                index
                            ],

                        "actual":
                            None
                    }
                )

        if len(actual) > common_length:

            for index in range(
                common_length,
                len(actual)
            ):

                current_path = (
                    f"{path}[{index}]"
                    if path
                    else f"[{index}]"
                )

                differences.append(
                    {
                        "path":
                            current_path,

                        "difference_type":
                            "UNEXPECTED_IN_ACTUAL",

                        "expected":
                            None,

                        "actual":
                            actual[
                                index
                            ]
                    }
                )

    # =========================================================
    # ATTRIBUTE INVESTIGATION
    # =========================================================

    def _investigate_attribute(
        self,
        http_method: str,
        endpoint: str,
        attribute: str,
        related_differences: list[dict]
    ) -> dict:

        try:

            trace_result = (
                self.trace_service.trace(
                    http_method=http_method,
                    endpoint=endpoint,
                    attribute_name=attribute
                )
            )

            likely_locations = (
                self._find_likely_locations(
                    trace_result
                )
            )

            return {
                "attribute":
                    attribute,

                "status":
                    "TRACE_FOUND",

                "differences":
                    related_differences,

                "likely_code_locations":
                    likely_locations,

                "trace":
                    trace_result[
                        "trace"
                    ]
            }

        except Exception as exception:

            return {
                "attribute":
                    attribute,

                "status":
                    "TRACE_NOT_FOUND",

                "differences":
                    related_differences,

                "error":
                    str(exception),

                "likely_code_locations":
                    [],

                "trace":
                    []
            }

    # =========================================================
    # LIKELY DEFECT LOCATIONS
    # =========================================================

    def _find_likely_locations(
        self,
        trace_result: dict
    ) -> list[dict]:

        result = []

        trace = (
            trace_result.get(
                "trace",
                []
            )
        )

        for step in trace:

            if (
                step.get(
                    "direct_attribute_touch"
                )
                is not True
            ):
                continue

            evidence_items = (
                step.get(
                    "evidence",
                    []
                )
            )

            if evidence_items:

                for evidence in (
                    evidence_items
                ):

                    result.append(
                        {
                            "class_name":
                                step.get(
                                    "class_name"
                                ),

                            "method_name":
                                step.get(
                                    "method_name"
                                ),

                            "line_number":
                                evidence.get(
                                    "line_number"
                                ),

                            "usage_type":
                                evidence.get(
                                    "usage_type"
                                ),

                            "code":
                                evidence.get(
                                    "code"
                                ),

                            "reason":
                                self._build_reason(
                                    evidence.get(
                                        "usage_type"
                                    )
                                )
                        }
                    )

            else:

                result.append(
                    {
                        "class_name":
                            step.get(
                                "class_name"
                            ),

                        "method_name":
                            step.get(
                                "method_name"
                            ),

                        "line_number":
                            None,

                        "usage_type":
                            None,

                        "code":
                            None,

                        "reason":
                            (
                                "This method directly "
                                "participates in the "
                                "attribute flow."
                            )
                    }
                )

        return result

    def _build_reason(
        self,
        usage_type: str | None
    ) -> str:

        if usage_type == "MAPPING":

            return (
                "The attribute is mapped from one "
                "object to another here."
            )

        if usage_type == "WRITE":

            return (
                "The attribute is written to an "
                "object here."
            )

        if usage_type == "READ":

            return (
                "The attribute is read from an "
                "object here."
            )

        if usage_type == "ASSIGNMENT":

            return (
                "The attribute is assigned a value "
                "here."
            )

        if usage_type == "CONDITION":

            return (
                "The attribute participates in a "
                "conditional decision here."
            )

        if usage_type == "RETURN":

            return (
                "The attribute participates in the "
                "returned value here."
            )

        return (
            "This code directly references "
            "the affected attribute."
        )

    # =========================================================
    # PATH HELPERS
    # =========================================================

    def _join_path(
        self,
        parent: str,
        child: str
    ) -> str:

        if not parent:
            return child

        return (
            f"{parent}.{child}"
        )

    def _display_path(
        self,
        path: str
    ) -> str:

        if not path:
            return "$"

        return path

    def _extract_attribute(
        self,
        path: str
    ) -> str | None:

        if not path:
            return None

        if path == "$":
            return None

        normalized = (
            path.strip()
        )

        normalized = (
            normalized.rstrip(
                "."
            )
        )

        parts = (
            normalized.split(
                "."
            )
        )

        if not parts:
            return None

        last_part = (
            parts[-1]
        )

        last_part = (
            self._remove_list_indexes(
                last_part
            )
        )

        if last_part:
            return last_part

        if len(parts) > 1:

            previous = (
                self._remove_list_indexes(
                    parts[-2]
                )
            )

            return (
                previous
                or None
            )

        return None

    def _remove_list_indexes(
        self,
        value: str
    ) -> str:

        result = value

        while "[" in result:

            start = (
                result.find(
                    "["
                )
            )

            end = (
                result.find(
                    "]",
                    start
                )
            )

            if (
                start == -1
                or end == -1
            ):
                break

            result = (
                result[:start]
                + result[
                    end + 1:
                ]
            )

        return (
            result.strip()
        )