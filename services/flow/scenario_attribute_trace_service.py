import re
from pathlib import Path

from config import settings
from services.flow.endpoint_flow_service import EndpointFlowService
from services.lineage.attribute_lineage_service import AttributeLineageService


class ScenarioAttributeTraceService:

    IGNORED_TYPES = {
        "void",
        "Void",
        "boolean",
        "byte",
        "short",
        "int",
        "long",
        "float",
        "double",
        "char",
        "Boolean",
        "Byte",
        "Short",
        "Integer",
        "Long",
        "Float",
        "Double",
        "Character",
        "String",
        "Object",
        "ResponseEntity",
        "HttpEntity",
        "Optional",
        "List",
        "Set",
        "Collection",
        "Iterable",
        "Map",
        "Page",
        "Slice"
    }

    def __init__(self):

        if not settings.JAVA_PROJECT_PATH:
            raise RuntimeError(
                "JAVA_PROJECT_PATH is not configured"
            )

        self.project_path = Path(
            settings.JAVA_PROJECT_PATH
        )

        self.endpoint_flow_service = (
            EndpointFlowService()
        )

        self.attribute_lineage_service = (
            AttributeLineageService()
        )

        self.class_contents = {}
        self.class_files = {}

        self._load_classes()

    # =========================================================
    # PUBLIC
    # =========================================================

    def trace(
        self,
        http_method: str,
        endpoint: str,
        attribute_name: str,
        input_context: dict | None = None
    ) -> dict:

        http_method = (
            http_method.upper().strip()
        )

        endpoint = endpoint.strip()

        attribute_name = (
            attribute_name.strip()
        )

        if not attribute_name:
            raise RuntimeError(
                "attribute_name is required"
            )

        endpoint_result = (
            self.endpoint_flow_service
            .analyze_endpoint(
                http_method=http_method,
                endpoint=endpoint
            )
        )

        attribute_result = (
            self.attribute_lineage_service
            .analyze(
                attribute_name=attribute_name
            )
        )

        occurrences = (
            attribute_result[
                "occurrences"
            ]
        )

        direct_attribute_methods = (
            self._extract_direct_methods(
                occurrences
            )
        )

        preferred_domains = self._infer_preferred_domains(
            endpoint=endpoint,
            input_context=input_context
        )

        if preferred_domains:
            direct_attribute_methods = self._filter_methods_for_domains(
                direct_methods=direct_attribute_methods,
                preferred_domains=preferred_domains
            )

        controller = (
            endpoint_result[
                "controller"
            ]
        )

        io_types = (
            self._resolve_endpoint_io_types(
                controller_file_path=(
                    controller[
                        "file_path"
                    ]
                ),
                method_name=(
                    controller[
                        "method_name"
                    ]
                )
            )
        )

        request_models = (
            self._expand_model_graph(
                io_types[
                    "request_root_types"
                ]
            )
        )

        response_models = (
            self._expand_model_graph(
                io_types[
                    "response_root_types"
                ]
            )
        )

        request_fields = (
            self._filter_fields_for_models(
                occurrences=occurrences,
                models=request_models
            )
        )

        response_fields = (
            self._filter_fields_for_models(
                occurrences=occurrences,
                models=response_models
            )
        )

        entity_fields = (
            self._filter_entity_fields(
                occurrences
            )
        )

        filtered_flow = (
            self._filter_flow_tree(
                node=endpoint_result[
                    "flow"
                ],
                direct_methods=(
                    direct_attribute_methods
                ),
                http_method=http_method,
                preferred_domains=preferred_domains,
                force_root=True
            )
        )

        execution_steps = []

        if filtered_flow:
            self._flatten_flow(
                node=filtered_flow,
                result=execution_steps
            )

        execution_steps = self._enrich_execution_steps_with_direct_methods(
            execution_steps=execution_steps,
            direct_methods=direct_attribute_methods,
            occurrences=occurrences,
            preferred_domains=preferred_domains,
        )

        trace_steps = (
            self._build_trace_steps(
                attribute_name=attribute_name,
                request_fields=request_fields,
                entity_fields=entity_fields,
                response_fields=response_fields,
                execution_steps=execution_steps,
                direct_methods=(
                    direct_attribute_methods
                ),
                occurrences=occurrences
            )
        )

        return {
            "http_method":
                http_method,

            "endpoint":
                endpoint,

            "attribute":
                attribute_name,

            "controller":
                controller,

            "request_root_types":
                sorted(
                    io_types[
                        "request_root_types"
                    ]
                ),

            "response_root_types":
                sorted(
                    io_types[
                        "response_root_types"
                    ]
                ),

            "request_models":
                sorted(
                    request_models
                ),

            "response_models":
                sorted(
                    response_models
                ),

            "direct_attribute_methods":
                sorted(
                    direct_attribute_methods
                ),

            "trace":
                trace_steps,

            "filtered_flow":
                filtered_flow
        }

    # =========================================================
    # PROJECT LOADING
    # =========================================================

    def _load_classes(
        self
    ):

        java_files = list(
            self.project_path.rglob(
                "*.java"
            )
        )

        for java_file in java_files:

            content = java_file.read_text(
                encoding="utf-8",
                errors="ignore"
            )

            class_name = (
                self._extract_class_name(
                    content
                )
            )

            if not class_name:
                continue

            self.class_contents[
                class_name
            ] = content

            self.class_files[
                class_name
            ] = java_file

    # =========================================================
    # DIRECT ATTRIBUTE METHODS
    # =========================================================

    def _extract_direct_methods(
        self,
        occurrences: list[dict]
    ) -> set[str]:

        result = set()

        for occurrence in occurrences:

            if (
                occurrence.get(
                    "location_type"
                )
                != "METHOD"
            ):
                continue

            class_name = (
                occurrence.get(
                    "class_name"
                )
            )

            method_name = (
                occurrence.get(
                    "method_name"
                )
            )

            if (
                not class_name
                or not method_name
            ):
                continue

            result.add(
                f"{class_name}.{method_name}"
            )

        return result

    # =========================================================
    # ENDPOINT REQUEST / RESPONSE TYPES
    # =========================================================

    def _resolve_endpoint_io_types(
        self,
        controller_file_path: str,
        method_name: str
    ) -> dict:

        content = Path(
            controller_file_path
        ).read_text(
            encoding="utf-8",
            errors="ignore"
        )

        methods = (
            self._extract_controller_methods(
                content
            )
        )

        for method in methods:

            if (
                method[
                    "method_name"
                ]
                != method_name
            ):
                continue

            request_types = (
                self._extract_request_body_types(
                    method[
                        "parameters"
                    ]
                )
            )

            response_types = (
                self._extract_domain_types(
                    method[
                        "return_type"
                    ]
                )
            )

            return {
                "request_root_types":
                    set(request_types),

                "response_root_types":
                    set(response_types)
            }

        return {
            "request_root_types":
                set(),

            "response_root_types":
                set()
        }

    def _extract_controller_methods(
        self,
        content: str
    ) -> list[dict]:

        result = []

        pattern = re.compile(
            r"""
            (?P<annotations>
                (?:
                    \s*
                    @[\w.]+
                    (?:\s*\([^)]*\))?
                    \s*
                )+
            )

            public
            \s+

            (?P<return_type>
                [\w<>\[\],.? ]+
            )

            \s+

            (?P<method_name>
                \w+
            )

            \s*
            \(

            (?P<parameters>
                [^)]*
            )

            \)
            """,
            re.VERBOSE |
            re.MULTILINE
        )

        for match in pattern.finditer(
            content
        ):

            result.append(
                {
                    "annotations":
                        match.group(
                            "annotations"
                        ),

                    "return_type":
                        match.group(
                            "return_type"
                        ).strip(),

                    "method_name":
                        match.group(
                            "method_name"
                        ),

                    "parameters":
                        match.group(
                            "parameters"
                        )
                }
            )

        return result

    def _extract_request_body_types(
        self,
        parameters: str
    ) -> list[str]:

        result = []

        parts = (
            self._split_parameters(
                parameters
            )
        )

        for parameter in parts:

            if "@RequestBody" not in parameter:
                continue

            cleaned = re.sub(
                r"""
                @\w+
                (?:\s*\([^)]*\))?
                """,
                "",
                parameter,
                flags=re.VERBOSE
            ).strip()

            tokens = cleaned.split()

            if len(tokens) < 2:
                continue

            type_expression = " ".join(
                tokens[:-1]
            )

            types = (
                self._extract_domain_types(
                    type_expression
                )
            )

            for type_name in types:

                if (
                    type_name
                    not in result
                ):
                    result.append(
                        type_name
                    )

        return result

    # =========================================================
    # SCENARIO-SPECIFIC MODEL GRAPH
    # =========================================================

    def _expand_model_graph(
        self,
        root_types: set[str]
    ) -> set[str]:

        discovered = set()
        pending = list(
            root_types
        )

        while pending:

            current = (
                pending.pop()
            )

            if current in discovered:
                continue

            if (
                current
                not in self.class_contents
            ):
                continue

            discovered.add(
                current
            )

            content = (
                self.class_contents[
                    current
                ]
            )

            field_types = (
                self._extract_field_types(
                    content
                )
            )

            for field_type in (
                field_types
            ):

                if (
                    field_type
                    not in discovered
                ):
                    pending.append(
                        field_type
                    )

        return discovered

    def _extract_field_types(
        self,
        content: str
    ) -> list[str]:

        result = []

        pattern = re.compile(
            r"""
            (?:
                private
                |
                protected
                |
                public
            )

            \s+

            (?P<type>
                [\w<>\[\],.?]+
            )

            \s+

            \w+

            \s*

            [;=]
            """,
            re.VERBOSE
        )

        for match in pattern.finditer(
            content
        ):

            type_expression = (
                match.group(
                    "type"
                )
            )

            domain_types = (
                self._extract_domain_types(
                    type_expression
                )
            )

            for domain_type in (
                domain_types
            ):

                if (
                    domain_type
                    not in result
                ):
                    result.append(
                        domain_type
                    )

        return result

    # =========================================================
    # DOMAIN / BRANCH SELECTION
    # =========================================================

    def _infer_preferred_domains(
        self,
        endpoint: str,
        input_context: dict | None
    ) -> set[str]:
        """
        Infer the business branch from endpoint and request JSON.

        This is intentionally conservative.  It only selects a domain when
        there is explicit evidence such as /employees, type=EMPLOYEE, or an
        employeeCode/studentCode field.
        """
        domains = set()
        endpoint_lower = (endpoint or "").lower()

        if "employee" in endpoint_lower:
            domains.add("employee")
        if "student" in endpoint_lower:
            domains.add("student")

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    key_lower = str(key).lower()

                    if key_lower in {"type", "persontype", "person_type", "entitytype", "entity_type"}:
                        item_lower = str(item).lower()
                        if "employee" in item_lower:
                            domains.add("employee")
                        if "student" in item_lower:
                            domains.add("student")

                    if "employeecode" in key_lower or key_lower == "employeeid":
                        domains.add("employee")
                    if "studentcode" in key_lower or key_lower == "studentid":
                        domains.add("student")

                    walk(item)

            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(input_context or {})

        # Conflicting evidence means we should not guess.
        if len(domains) > 1:
            return set()

        return domains

    def _domain_for_class(
        self,
        class_name: str | None
    ) -> str | None:
        name = (class_name or "").lower()

        if "employee" in name:
            return "employee"
        if "student" in name:
            return "student"

        return None

    def _is_explicitly_other_domain_class(
        self,
        class_name: str | None,
        preferred_domains: set[str]
    ) -> bool:
        class_domain = self._domain_for_class(class_name)

        return (
            class_domain is not None
            and class_domain not in preferred_domains
        )

    def _filter_methods_for_domains(
        self,
        direct_methods: set[str],
        preferred_domains: set[str]
    ) -> set[str]:
        """
        Remove direct attribute methods that clearly belong to a sibling domain.
        Shared mappers/entities (ContactDetailsMapper, EmailDetailsMapper, etc.)
        remain because they have no Employee/Student marker in the class name.
        """
        result = set()

        for full_method in direct_methods:
            class_name = full_method.split(".", 1)[0]
            class_domain = self._domain_for_class(class_name)

            if (
                class_domain is None
                or class_domain in preferred_domains
            ):
                result.add(full_method)

        return result

    # =========================================================
    # FLOW FILTERING
    # =========================================================

    def _filter_flow_tree(
        self,
        node: dict,
        direct_methods: set[str],
        http_method: str,
        preferred_domains: set[str] | None = None,
        force_root: bool = False
    ) -> dict | None:

        if not node:
            return None

        class_name = (
            node.get(
                "class_name"
            )
        )

        method_name = (
            node.get(
                "method_name"
            )
        )

        # When the request itself identifies a domain (for example
        # type=EMPLOYEE or employeeCode), do not let a shared controller trace
        # wander into sibling Student/Employee branches.
        if (
            not force_root
            and preferred_domains
            and self._is_explicitly_other_domain_class(
                class_name=class_name,
                preferred_domains=preferred_domains
            )
        ):
            return None

        full_method = None

        if (
            class_name
            and method_name
        ):
            full_method = (
                f"{class_name}."
                f"{method_name}"
            )

        filtered_children = []

        for child in (
            node.get(
                "calls",
                []
            )
        ):

            filtered_child = (
                self._filter_flow_tree(
                    node=child,
                    direct_methods=(
                        direct_methods
                    ),
                    http_method=http_method,
                    preferred_domains=preferred_domains
                )
            )

            if filtered_child:
                filtered_children.append(
                    filtered_child
                )

        direct_match = (
            full_method
            in direct_methods
        )

        repository_match = (
            self._is_relevant_repository_node(
                node=node,
                http_method=http_method
            )
        )

        bridge_match = bool(
            filtered_children
        )

        keep = (
            force_root
            or direct_match
            or repository_match
            or bridge_match
        )

        if not keep:
            return None

        filtered = {
            "class_name":
                class_name,

            "method_name":
                method_name,

            "found":
                node.get(
                    "found",
                    True
                ),

            "calls":
                filtered_children
        }

        for key in (
            "type",
            "framework",
            "operation"
        ):

            if key in node:
                filtered[key] = (
                    node[key]
                )

        if direct_match:

            filtered[
                "relevance"
            ] = (
                "DIRECT_ATTRIBUTE_TOUCH"
            )

        elif repository_match:

            filtered[
                "relevance"
            ] = (
                "PERSISTENCE_BOUNDARY"
            )

        else:

            filtered[
                "relevance"
            ] = "BRIDGE"

        return filtered

    def _is_relevant_repository_node(
        self,
        node: dict,
        http_method: str
    ) -> bool:

        if (
            node.get(
                "type"
            )
            != "REPOSITORY"
        ):
            return False

        operation = (
            node.get(
                "operation"
            )
        )

        if (
            http_method
            in {
                "POST",
                "PUT",
                "PATCH"
            }
        ):
            return (
                operation
                == "WRITE"
            )

        if http_method == "DELETE":
            return (
                operation
                == "DELETE"
            )

        if http_method == "GET":
            return (
                operation
                == "READ"
            )

        return False

    # =========================================================
    # FLOW FLATTENING
    # =========================================================

    def _flatten_flow(
        self,
        node: dict,
        result: list[dict]
    ):

        result.append(
            {
                "class_name":
                    node.get(
                        "class_name"
                    ),

                "method_name":
                    node.get(
                        "method_name"
                    ),

                "type":
                    node.get(
                        "type",
                        "METHOD"
                    ),

                "operation":
                    node.get(
                        "operation"
                    ),

                "relevance":
                    node.get(
                        "relevance"
                    )
            }
        )

        for child in (
            node.get(
                "calls",
                []
            )
        ):

            self._flatten_flow(
                child,
                result
            )

    def _enrich_execution_steps_with_direct_methods(
        self,
        execution_steps: list[dict],
        direct_methods: set[str],
        occurrences: list[dict],
        preferred_domains: set[str] | None = None,
    ) -> list[dict]:
        """Restore attribute-touching methods that are absent from the call graph.

        Static call graphs often stop at a service/repository boundary and can miss
        mapper/converter calls made through helper methods.  Attribute lineage has
        stronger evidence for those methods, so inject the missing direct-touch
        methods immediately before the persistence boundary.  They are marked as
        DIRECT_ATTRIBUTE_TOUCH and therefore become visible/highlighted in the
        defect flow instead of reporting "0 likely modification points".
        """
        result = [dict(step) for step in execution_steps]
        existing = {
            f"{step.get('class_name')}.{step.get('method_name')}"
            for step in result
            if step.get("class_name") and step.get("method_name")
        }

        evidence_by_method = {}
        for occurrence in occurrences:
            class_name = occurrence.get("class_name")
            method_name = occurrence.get("method_name")
            if not class_name or not method_name:
                continue
            full_method = f"{class_name}.{method_name}"
            evidence_by_method.setdefault(full_method, occurrence)

        missing = []
        for full_method in sorted(direct_methods):
            if full_method in existing:
                continue
            class_name, method_name = full_method.split(".", 1)
            if preferred_domains and self._is_explicitly_other_domain_class(
                class_name, preferred_domains
            ):
                continue
            occurrence = evidence_by_method.get(full_method, {})
            roles = occurrence.get("class_roles") or []
            role_priority = 0
            if "MAPPER" in roles or "CONVERTER" in roles:
                role_priority = 30
            elif "SERVICE" in roles:
                role_priority = 20
            elif "ENTITY" in roles or "MODEL" in roles:
                role_priority = 10
            missing.append((
                -role_priority,
                int(occurrence.get("line_number") or 0),
                {
                    "class_name": class_name,
                    "method_name": method_name,
                    "type": "METHOD",
                    "operation": None,
                    "relevance": "DIRECT_ATTRIBUTE_TOUCH",
                },
            ))

        missing.sort(key=lambda item: (item[0], item[1], item[2]["class_name"], item[2]["method_name"]))
        additions = [item[2] for item in missing]
        if not additions:
            return result

        repository_index = next(
            (index for index, step in enumerate(result) if step.get("type") == "REPOSITORY"),
            len(result),
        )
        return result[:repository_index] + additions + result[repository_index:]

    # =========================================================
    # FIELD FILTERING
    # =========================================================

    def _filter_fields_for_models(
        self,
        occurrences: list[dict],
        models: set[str]
    ) -> list[dict]:

        result = []

        for occurrence in occurrences:

            if (
                occurrence.get(
                    "location_type"
                )
                != "FIELD"
            ):
                continue

            class_name = (
                occurrence.get(
                    "class_name"
                )
            )

            if class_name not in models:
                continue

            result.append(
                occurrence
            )

        return result

    def _filter_entity_fields(
        self,
        occurrences: list[dict]
    ) -> list[dict]:

        result = []

        for occurrence in occurrences:

            if (
                occurrence.get(
                    "location_type"
                )
                != "FIELD"
            ):
                continue

            roles = (
                occurrence.get(
                    "class_roles",
                    []
                )
            )

            if "ENTITY" not in roles:
                continue

            result.append(
                occurrence
            )

        return result

    # =========================================================
    # TRACE BUILDING
    # =========================================================

    def _build_trace_steps(
        self,
        attribute_name: str,
        request_fields: list[dict],
        entity_fields: list[dict],
        response_fields: list[dict],
        execution_steps: list[dict],
        direct_methods: set[str],
        occurrences: list[dict]
    ) -> list[dict]:

        trace = []

        order = 1

        for field in request_fields:

            trace.append(
                {
                    "order":
                        order,

                    "step_type":
                        "REQUEST_FIELD",

                    "label":
                        (
                            f"{field['class_name']}."
                            f"{attribute_name}"
                        ),

                    "class_name":
                        field[
                            "class_name"
                        ],

                    "attribute":
                        attribute_name,

                    "line_number":
                        field[
                            "line_number"
                        ],

                    "code":
                        field[
                            "code"
                        ]
                }
            )

            order += 1

        entity_inserted = False

        for step in execution_steps:

            if (
                step.get(
                    "type"
                )
                == "REPOSITORY"
                and not entity_inserted
            ):

                for field in entity_fields:

                    trace.append(
                        {
                            "order":
                                order,

                            "step_type":
                                "ENTITY_FIELD",

                            "label":
                                (
                                    f"{field['class_name']}."
                                    f"{attribute_name}"
                                ),

                            "class_name":
                                field[
                                    "class_name"
                                ],

                            "attribute":
                                attribute_name,

                            "line_number":
                                field[
                                    "line_number"
                                ],

                            "code":
                                field[
                                    "code"
                                ]
                        }
                    )

                    order += 1

                entity_inserted = True

            full_method = (
                f"{step['class_name']}."
                f"{step['method_name']}"
            )

            evidence = (
                self._method_evidence(
                    full_method=full_method,
                    occurrences=occurrences
                )
            )

            trace.append(
                {
                    "order":
                        order,

                    "step_type":
                        (
                            "REPOSITORY"
                            if (
                                step.get(
                                    "type"
                                )
                                == "REPOSITORY"
                            )
                            else "METHOD"
                        ),

                    "label":
                        full_method,

                    "class_name":
                        step[
                            "class_name"
                        ],

                    "method_name":
                        step[
                            "method_name"
                        ],

                    "relevance":
                        step.get(
                            "relevance"
                        ),

                    "operation":
                        step.get(
                            "operation"
                        ),

                    "direct_attribute_touch":
                        (
                            full_method
                            in direct_methods
                        ),

                    "evidence":
                        evidence
                }
            )

            order += 1

        if (
            not entity_inserted
            and entity_fields
        ):

            for field in entity_fields:

                trace.append(
                    {
                        "order":
                            order,

                        "step_type":
                            "ENTITY_FIELD",

                        "label":
                            (
                                f"{field['class_name']}."
                                f"{attribute_name}"
                            ),

                        "class_name":
                            field[
                                "class_name"
                            ],

                        "attribute":
                            attribute_name,

                        "line_number":
                            field[
                                "line_number"
                            ],

                        "code":
                            field[
                                "code"
                            ]
                    }
                )

                order += 1

        for field in response_fields:

            trace.append(
                {
                    "order":
                        order,

                    "step_type":
                        "RESPONSE_FIELD",

                    "label":
                        (
                            f"{field['class_name']}."
                            f"{attribute_name}"
                        ),

                    "class_name":
                        field[
                            "class_name"
                        ],

                    "attribute":
                        attribute_name,

                    "line_number":
                        field[
                            "line_number"
                        ],

                    "code":
                        field[
                            "code"
                        ]
                }
            )

            order += 1

        return trace

    def _method_evidence(
        self,
        full_method: str,
        occurrences: list[dict]
    ) -> list[dict]:

        result = []

        for occurrence in occurrences:

            if (
                occurrence.get(
                    "location_type"
                )
                != "METHOD"
            ):
                continue

            class_name = (
                occurrence.get(
                    "class_name"
                )
            )

            method_name = (
                occurrence.get(
                    "method_name"
                )
            )

            occurrence_method = (
                f"{class_name}."
                f"{method_name}"
            )

            if (
                occurrence_method
                != full_method
            ):
                continue

            result.append(
                {
                    "line_number":
                        occurrence.get(
                            "line_number"
                        ),

                    "usage_type":
                        occurrence.get(
                            "usage_type"
                        ),

                    "code":
                        occurrence.get(
                            "code"
                        )
                }
            )

        return result

    # =========================================================
    # TYPE HELPERS
    # =========================================================

    def _extract_domain_types(
        self,
        type_expression: str
    ) -> list[str]:

        identifiers = re.findall(
            r"\b[A-Z]\w*\b",
            type_expression
        )

        result = []

        for identifier in identifiers:

            if (
                identifier
                in self.IGNORED_TYPES
            ):
                continue

            if (
                identifier
                not in self.class_contents
            ):
                continue

            if (
                identifier
                not in result
            ):
                result.append(
                    identifier
                )

        return result

    def _split_parameters(
        self,
        parameters: str
    ) -> list[str]:

        result = []

        current = []

        generic_depth = 0
        annotation_depth = 0

        for character in parameters:

            if character == "<":
                generic_depth += 1

            elif character == ">":
                generic_depth = max(
                    0,
                    generic_depth - 1
                )

            elif character == "(":
                annotation_depth += 1

            elif character == ")":
                annotation_depth = max(
                    0,
                    annotation_depth - 1
                )

            if (
                character == ","
                and generic_depth == 0
                and annotation_depth == 0
            ):

                result.append(
                    "".join(
                        current
                    ).strip()
                )

                current = []

                continue

            current.append(
                character
            )

        if current:

            result.append(
                "".join(
                    current
                ).strip()
            )

        return result

    # =========================================================
    # CLASS HELPERS
    # =========================================================

    def _extract_class_name(
        self,
        content: str
    ) -> str | None:

        match = re.search(
            r"""
            \b
            (?:
                class
                |
                interface
                |
                record
            )
            \s+
            (\w+)
            """,
            content,
            re.VERBOSE
        )

        if not match:
            return None

        return match.group(1)