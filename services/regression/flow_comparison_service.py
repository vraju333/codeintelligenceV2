
from typing import Any


class FlowComparisonService:

    def compare(
        self,
        baseline_flow: dict | None,
        current_flow: dict | None
    ):

        if not baseline_flow:
            return {
                "status": "BASELINE_FLOW_MISSING",
                "flow_changed": False,
                "baseline_methods": [],
                "current_methods": (
                    self.extract_methods(current_flow)
                    if current_flow
                    else []
                ),
                "added_methods": [],
                "removed_methods": []
            }

        baseline_methods = (
            self.extract_methods(
                baseline_flow
            )
        )

        current_methods = (
            self.extract_methods(
                current_flow
            )
        )

        baseline_set = set(
            baseline_methods
        )

        current_set = set(
            current_methods
        )

        added = sorted(
            current_set - baseline_set
        )

        removed = sorted(
            baseline_set - current_set
        )

        flow_changed = bool(
            added or removed
        )

        return {
            "status": (
                "FLOW_CHANGED"
                if flow_changed
                else "FLOW_UNCHANGED"
            ),
            "flow_changed": flow_changed,
            "baseline_methods":
                baseline_methods,
            "current_methods":
                current_methods,
            "added_methods":
                added,
            "removed_methods":
                removed
        }

    def extract_methods(
        self,
        flow_data: Any
    ):

        methods = []

        if not flow_data:
            return methods

        root = flow_data.get(
            "flow",
            flow_data
        )

        self._walk_flow(
            root,
            methods
        )

        return methods

    def _walk_flow(
        self,
        node: Any,
        methods: list[str]
    ):

        if not isinstance(
            node,
            dict
        ):
            return

        class_name = node.get(
            "class_name"
        )

        method_name = node.get(
            "method_name"
        )

        if class_name and method_name:

            value = (
                f"{class_name}."
                f"{method_name}"
            )

            if value not in methods:
                methods.append(
                    value
                )

        for child in node.get(
            "calls",
            []
        ):
            self._walk_flow(
                child,
                methods
            )