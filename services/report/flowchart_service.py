class FlowchartService:

    def generate(
        self,
        flow_data: dict
    ):

        root = flow_data.get(
            "flow",
            flow_data
        )

        if not root:

            return {
                "status": "FLOW_NOT_FOUND",
                "mermaid": ""
            }

        self.node_counter = 0
        self.nodes = []
        self.edges = []

        self._walk(
            node=root,
            parent_id=None
        )

        mermaid_lines = [
            "flowchart TD"
        ]

        for node in self.nodes:

            mermaid_lines.append(
                f'    {node["id"]}["{node["label"]}"]'
            )

        mermaid_lines.append("")

        for edge in self.edges:

            mermaid_lines.append(
                f'    {edge["from"]} --> {edge["to"]}'
            )

        mermaid = "\n".join(
            mermaid_lines
        )

        return {
            "status": "FLOWCHART_GENERATED",
            "total_nodes": len(
                self.nodes
            ),
            "total_edges": len(
                self.edges
            ),
            "nodes": self.nodes,
            "edges": self.edges,
            "mermaid": mermaid
        }

    def _walk(
        self,
        node: dict,
        parent_id: str | None
    ):

        node_id = self._next_id()

        class_name = node.get(
            "class_name",
            "UnknownClass"
        )

        method_name = node.get(
            "method_name",
            "unknownMethod"
        )

        node_type = node.get(
            "type"
        )

        operation = node.get(
            "operation"
        )

        label = (
            f"{class_name}.{method_name}"
        )

        if node_type == "REPOSITORY":

            if operation:

                label += (
                    f"\\n[{operation}]"
                )
            else:

                label += (
                    "\\n[REPOSITORY]"
                )

        self.nodes.append(
            {
                "id": node_id,
                "label": self._escape_label(
                    label
                ),
                "class_name": class_name,
                "method_name": method_name,
                "type": node_type,
                "operation": operation,
                "input_parameters": node.get("input_parameters", []),
                "return_type": node.get("return_type"),
                "file_path": node.get("file_path"),
                "signature_owner": node.get("signature_owner"),
                "external": bool(node.get("external")),
            }
        )

        if parent_id:

            self.edges.append(
                {
                    "from": parent_id,
                    "to": node_id
                }
            )

        for child in node.get(
            "calls",
            []
        ):

            self._walk(
                node=child,
                parent_id=node_id
            )

    def _next_id(
        self
    ):

        self.node_counter += 1

        return (
            f"N{self.node_counter}"
        )

    def _escape_label(
        self,
        value: str
    ):

        return (
            value
            .replace(
                '"',
                "'"
            )
        )