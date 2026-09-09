import re
from pathlib import Path

from config import settings


class AttributeLineageService:

    HTTP_MAPPING_ANNOTATIONS = {
        "GetMapping",
        "PostMapping",
        "PutMapping",
        "PatchMapping",
        "DeleteMapping",
        "RequestMapping"
    }

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

        self.java_files = list(
            self.project_path.rglob(
                "*.java"
            )
        )

        self.class_contents = {}
        self.class_files = {}
        self.class_roles = {}

        self._load_classes()

        self._discover_class_roles()

        self._propagate_model_roles()

    # =========================================================
    # PUBLIC
    # =========================================================

    def analyze(
        self,
        attribute_name: str
    ) -> dict:

        attribute_name = (
            attribute_name.strip()
        )

        if not attribute_name:
            raise RuntimeError(
                "attribute_name is required"
            )

        occurrences = []

        for class_name, content in (
            self.class_contents.items()
        ):

            if not self._contains_attribute(
                content=content,
                attribute_name=attribute_name
            ):
                continue

            java_file = (
                self.class_files[
                    class_name
                ]
            )

            package_name = (
                self._extract_package_name(
                    content
                )
            )

            roles = (
                self.class_roles.get(
                    class_name,
                    {"JAVA_CLASS"}
                )
            )

            primary_role = (
                self._primary_role(
                    roles
                )
            )

            field_occurrences = (
                self._find_field_occurrences(
                    content=content,
                    attribute_name=attribute_name
                )
            )

            for field_occurrence in (
                field_occurrences
            ):

                occurrences.append(
                    {
                        "file_name":
                            java_file.name,

                        "file_path":
                            str(java_file),

                        "package_name":
                            package_name,

                        "class_name":
                            class_name,

                        "class_role":
                            primary_role,

                        "class_roles":
                            sorted(roles),

                        "location_type":
                            "FIELD",

                        "method_name":
                            None,

                        "line_number":
                            field_occurrence[
                                "line_number"
                            ],

                        "code":
                            field_occurrence[
                                "code"
                            ],

                        "usage_type":
                            self._classify_field_usage(
                                roles
                            )
                    }
                )

            method_occurrences = (
                self._find_method_occurrences(
                    content=content,
                    attribute_name=attribute_name
                )
            )

            for method_occurrence in (
                method_occurrences
            ):

                occurrences.append(
                    {
                        "file_name":
                            java_file.name,

                        "file_path":
                            str(java_file),

                        "package_name":
                            package_name,

                        "class_name":
                            class_name,

                        "class_role":
                            primary_role,

                        "class_roles":
                            sorted(roles),

                        "location_type":
                            "METHOD",

                        "method_name":
                            method_occurrence[
                                "method_name"
                            ],

                        "line_number":
                            method_occurrence[
                                "line_number"
                            ],

                        "code":
                            method_occurrence[
                                "code"
                            ],

                        "usage_type":
                            method_occurrence[
                                "usage_type"
                            ]
                    }
                )

        occurrences.sort(
            key=self._sort_occurrence
        )

        return {
            "attribute":
                attribute_name,

            "total_occurrences":
                len(occurrences),

            "occurrences":
                occurrences,

            "summary":
                self._build_summary(
                    occurrences
                ),

            "relevant_model_roles":
                self._relevant_roles(
                    occurrences
                )
        }

    # =========================================================
    # PROJECT LOADING
    # =========================================================

    def _load_classes(
        self
    ):

        for java_file in self.java_files:

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

            self.class_roles.setdefault(
                class_name,
                set()
            )

    # =========================================================
    # ROLE DISCOVERY
    # =========================================================

    def _discover_class_roles(
        self
    ):

        self._discover_annotation_roles()

        self._discover_controller_roles()

        for class_name in (
            self.class_roles
        ):

            if not self.class_roles[
                class_name
            ]:

                self.class_roles[
                    class_name
                ].add(
                    "JAVA_CLASS"
                )

    def _discover_annotation_roles(
        self
    ):

        for class_name, content in (
            self.class_contents.items()
        ):

            roles = (
                self.class_roles[
                    class_name
                ]
            )

            if (
                "@Entity" in content
                or "@Table" in content
            ):
                roles.add(
                    "ENTITY"
                )

            if (
                "@RestController" in content
                or "@Controller" in content
            ):
                roles.add(
                    "CONTROLLER"
                )

            if "@Service" in content:
                roles.add(
                    "SERVICE"
                )

            if (
                "@Repository" in content
                or self._extends_repository(
                    content
                )
            ):
                roles.add(
                    "REPOSITORY"
                )

            if "@Mapper" in content:
                roles.add(
                    "MAPPER"
                )

            elif "@Component" in content:
                roles.add(
                    "COMPONENT"
                )

    def _extends_repository(
        self,
        content: str
    ) -> bool:

        pattern = re.compile(
            r"""
            extends
            \s+
            (?:
                JpaRepository
                |
                CrudRepository
                |
                PagingAndSortingRepository
            )
            \s*
            <
            """,
            re.VERBOSE
        )

        return bool(
            pattern.search(
                content
            )
        )

    # =========================================================
    # CONTROLLER MODEL DISCOVERY
    # =========================================================

    def _discover_controller_roles(
        self
    ):

        for controller_class, content in (
            self.class_contents.items()
        ):

            roles = self.class_roles.get(
                controller_class,
                set()
            )

            if "CONTROLLER" not in roles:
                continue

            methods = (
                self._extract_controller_methods(
                    content
                )
            )

            for method in methods:

                annotations = (
                    method[
                        "annotations"
                    ]
                )

                if not self._has_http_mapping(
                    annotations
                ):
                    continue

                request_types = (
                    self._extract_request_body_types(
                        method[
                            "parameters"
                        ]
                    )
                )

                for request_type in (
                    request_types
                ):

                    if (
                        request_type
                        in self.class_roles
                    ):

                        self.class_roles[
                            request_type
                        ].discard(
                            "JAVA_CLASS"
                        )

                        self.class_roles[
                            request_type
                        ].add(
                            "REQUEST_MODEL"
                        )

                response_types = (
                    self._extract_domain_types(
                        method[
                            "return_type"
                        ]
                    )
                )

                for response_type in (
                    response_types
                ):

                    if (
                        response_type
                        not in self.class_roles
                    ):
                        continue

                    self.class_roles[
                        response_type
                    ].discard(
                        "JAVA_CLASS"
                    )

                    self.class_roles[
                        response_type
                    ].add(
                        "RESPONSE_MODEL"
                    )

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

    def _has_http_mapping(
        self,
        annotations: str
    ) -> bool:

        for annotation in (
            self.HTTP_MAPPING_ANNOTATIONS
        ):

            if (
                f"@{annotation}"
                in annotations
            ):
                return True

        return False

    def _extract_request_body_types(
        self,
        parameters: str
    ) -> list[str]:

        result = []

        parameters_list = (
            self._split_parameters(
                parameters
            )
        )

        for parameter in (
            parameters_list
        ):

            if (
                "@RequestBody"
                not in parameter
            ):
                continue

            cleaned_parameter = re.sub(
                r"""
                @\w+
                (?:\s*\([^)]*\))?
                """,
                "",
                parameter,
                flags=re.VERBOSE
            ).strip()

            tokens = (
                cleaned_parameter.split()
            )

            if len(tokens) < 2:
                continue

            type_expression = " ".join(
                tokens[:-1]
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
    # NESTED MODEL PROPAGATION
    # =========================================================

    def _propagate_model_roles(
        self
    ):

        changed = True

        while changed:

            changed = False

            for class_name, roles in list(
                self.class_roles.items()
            ):

                parent_roles = set()

                if "REQUEST_MODEL" in roles:
                    parent_roles.add(
                        "REQUEST_MODEL"
                    )

                if "RESPONSE_MODEL" in roles:
                    parent_roles.add(
                        "RESPONSE_MODEL"
                    )

                if not parent_roles:
                    continue

                content = (
                    self.class_contents.get(
                        class_name
                    )
                )

                if not content:
                    continue

                nested_types = (
                    self._extract_field_types(
                        content
                    )
                )

                for nested_type in (
                    nested_types
                ):

                    if (
                        nested_type
                        not in self.class_roles
                    ):
                        continue

                    nested_roles = (
                        self.class_roles[
                            nested_type
                        ]
                    )

                    before = set(
                        nested_roles
                    )

                    nested_roles.discard(
                        "JAVA_CLASS"
                    )

                    nested_roles.update(
                        parent_roles
                    )

                    if (
                        before
                        != nested_roles
                    ):
                        changed = True

    def _extract_field_types(
        self,
        content: str
    ) -> list[str]:

        result = []

        field_pattern = re.compile(
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

        for match in field_pattern.finditer(
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
    # TYPE HANDLING
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

        for identifier in (
            identifiers
        ):

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
    # ATTRIBUTE DETECTION
    # =========================================================

    def _build_attribute_pattern(
        self,
        attribute_name: str
    ) -> re.Pattern:

        capitalized = (
            attribute_name[0].upper()
            + attribute_name[1:]
        )

        attribute = re.escape(
            attribute_name
        )

        property_name = re.escape(
            capitalized
        )

        return re.compile(
            rf"""
            (
                \b{attribute}\b

                |

                \bget{property_name}
                \s*
                \(

                |

                \bset{property_name}
                \s*
                \(

                |

                \bis{property_name}
                \s*
                \(
            )
            """,
            re.VERBOSE |
            re.IGNORECASE
        )

    def _contains_attribute(
        self,
        content: str,
        attribute_name: str
    ) -> bool:

        pattern = (
            self._build_attribute_pattern(
                attribute_name
            )
        )

        return bool(
            pattern.search(
                content
            )
        )

    # =========================================================
    # FIELD OCCURRENCES
    # =========================================================

    def _find_field_occurrences(
        self,
        content: str,
        attribute_name: str
    ) -> list[dict]:

        result = []

        field_pattern = re.compile(
            rf"""
            (?:
                private
                |
                protected
                |
                public
            )

            \s+

            [\w<>\[\],.?]+

            \s+

            {re.escape(attribute_name)}

            \s*

            [;=]
            """,
            re.VERBOSE |
            re.IGNORECASE
        )

        lines = (
            content.splitlines()
        )

        for line_number, line in enumerate(
            lines,
            start=1
        ):

            if field_pattern.search(
                line
            ):

                result.append(
                    {
                        "line_number":
                            line_number,

                        "code":
                            line.strip()
                    }
                )

        return result

    # =========================================================
    # METHOD OCCURRENCES
    # =========================================================

    def _find_method_occurrences(
        self,
        content: str,
        attribute_name: str
    ) -> list[dict]:

        result = []

        methods = (
            self._extract_methods(
                content
            )
        )

        attribute_pattern = (
            self._build_attribute_pattern(
                attribute_name
            )
        )

        for method in methods:

            body = (
                method[
                    "body"
                ]
            )

            if not attribute_pattern.search(
                body
            ):
                continue

            body_lines = (
                body.splitlines()
            )

            for offset, line in enumerate(
                body_lines
            ):

                if not attribute_pattern.search(
                    line
                ):
                    continue

                line_number = (
                    method[
                        "start_line"
                    ]
                    + offset
                )

                result.append(
                    {
                        "method_name":
                            method[
                                "method_name"
                            ],

                        "line_number":
                            line_number,

                        "code":
                            line.strip(),

                        "usage_type":
                            self._classify_method_usage(
                                line=line,
                                attribute_name=attribute_name
                            )
                    }
                )

        return result

    def _extract_methods(
        self,
        content: str
    ) -> list[dict]:

        result = []

        pattern = re.compile(
            r"""
            (?:
                public
                |
                protected
                |
                private
            )

            \s+

            (?:static\s+)?
            (?:final\s+)?
            (?:synchronized\s+)?
            (?:<[^>]+>\s+)?

            [\w<>\[\],.? ]+

            \s+

            (?P<method_name>
                \w+
            )

            \s*
            \(

            [^)]*

            \)

            \s*

            (?:throws\s+[^{]+)?

            \{
            """,
            re.VERBOSE |
            re.MULTILINE
        )

        for match in pattern.finditer(
            content
        ):

            method_name = (
                match.group(
                    "method_name"
                )
            )

            brace_start = (
                match.end()
                - 1
            )

            brace_end = (
                self._find_matching_brace(
                    content=content,
                    start_position=brace_start
                )
            )

            if brace_end is None:
                continue

            body = content[
                brace_start + 1:
                brace_end
            ]

            start_line = (
                content.count(
                    "\n",
                    0,
                    brace_start
                )
                + 1
            )

            result.append(
                {
                    "method_name":
                        method_name,

                    "body":
                        body,

                    "start_line":
                        start_line
                }
            )

        return result

    def _find_matching_brace(
        self,
        content: str,
        start_position: int
    ) -> int | None:

        depth = 0

        in_string = False
        in_character = False
        escape = False

        index = start_position

        while index < len(
            content
        ):

            character = (
                content[index]
            )

            if escape:

                escape = False

                index += 1

                continue

            if character == "\\":

                escape = True

                index += 1

                continue

            if (
                character == '"'
                and not in_character
            ):

                in_string = (
                    not in_string
                )

                index += 1

                continue

            if (
                character == "'"
                and not in_string
            ):

                in_character = (
                    not in_character
                )

                index += 1

                continue

            if (
                in_string
                or in_character
            ):

                index += 1

                continue

            if character == "{":

                depth += 1

            elif character == "}":

                depth -= 1

                if depth == 0:
                    return index

            index += 1

        return None

    # =========================================================
    # USAGE CLASSIFICATION
    # =========================================================

    def _classify_method_usage(
        self,
        line: str,
        attribute_name: str
    ) -> str:

        capitalized = (
            attribute_name[0].upper()
            + attribute_name[1:]
        )

        property_name = (
            re.escape(
                capitalized
            )
        )

        setter_pattern = re.compile(
            rf"\.set{property_name}\s*\(",
            re.IGNORECASE
        )

        getter_pattern = re.compile(
            rf"\.get{property_name}\s*\(",
            re.IGNORECASE
        )

        boolean_getter_pattern = re.compile(
            rf"\.is{property_name}\s*\(",
            re.IGNORECASE
        )

        direct_assignment_pattern = re.compile(
            rf"""
            \b
            {re.escape(attribute_name)}
            \b
            \s*
            =
            """,
            re.VERBOSE |
            re.IGNORECASE
        )

        has_setter = bool(
            setter_pattern.search(
                line
            )
        )

        has_getter = bool(
            getter_pattern.search(
                line
            )
            or boolean_getter_pattern.search(
                line
            )
        )

        if (
            has_setter
            and has_getter
        ):
            return "MAPPING"

        if has_setter:
            return "WRITE"

        if has_getter:
            return "READ"

        if direct_assignment_pattern.search(
            line
        ):
            return "ASSIGNMENT"

        if re.search(
            r"\bif\s*\(",
            line
        ):
            return "CONDITION"

        if line.strip().startswith(
            "return "
        ):
            return "RETURN"

        return "REFERENCE"

    def _classify_field_usage(
        self,
        roles: set[str]
    ) -> str:

        if (
            "REQUEST_MODEL" in roles
            and "RESPONSE_MODEL" in roles
        ):
            return "REQUEST_RESPONSE_FIELD"

        if "REQUEST_MODEL" in roles:
            return "REQUEST_FIELD"

        if "ENTITY" in roles:
            return "ENTITY_FIELD"

        if "RESPONSE_MODEL" in roles:
            return "RESPONSE_FIELD"

        return "FIELD"

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

    def _extract_package_name(
        self,
        content: str
    ) -> str | None:

        match = re.search(
            r"""
            \bpackage
            \s+
            ([\w.]+)
            \s*
            ;
            """,
            content,
            re.VERBOSE
        )

        if not match:
            return None

        return match.group(1)

    # =========================================================
    # ROLE PRIORITY
    # =========================================================

    def _primary_role(
        self,
        roles: set[str]
    ) -> str:

        priority = [
            "REQUEST_MODEL",
            "CONTROLLER",
            "SERVICE",
            "MAPPER",
            "COMPONENT",
            "ENTITY",
            "REPOSITORY",
            "RESPONSE_MODEL",
            "JAVA_CLASS"
        ]

        for role in priority:

            if role in roles:
                return role

        return "JAVA_CLASS"

    # =========================================================
    # SORTING
    # =========================================================

    def _sort_occurrence(
        self,
        occurrence: dict
    ) -> tuple:

        role_order = {
            "REQUEST_MODEL": 1,
            "CONTROLLER": 2,
            "SERVICE": 3,
            "MAPPER": 4,
            "COMPONENT": 4,
            "ENTITY": 5,
            "REPOSITORY": 6,
            "RESPONSE_MODEL": 7,
            "JAVA_CLASS": 8
        }

        return (
            role_order.get(
                occurrence[
                    "class_role"
                ],
                99
            ),

            occurrence[
                "class_name"
            ]
            or "",

            occurrence[
                "line_number"
            ]
        )

    # =========================================================
    # SUMMARY
    # =========================================================

    def _build_summary(
        self,
        occurrences: list[dict]
    ) -> dict:

        classes = []
        methods = []
        roles = []
        usage_types = []

        for occurrence in (
            occurrences
        ):

            class_name = (
                occurrence.get(
                    "class_name"
                )
            )

            if (
                class_name
                and class_name
                not in classes
            ):
                classes.append(
                    class_name
                )

            method_name = (
                occurrence.get(
                    "method_name"
                )
            )

            if method_name:

                full_method = (
                    f"{class_name}."
                    f"{method_name}"
                )

                if (
                    full_method
                    not in methods
                ):
                    methods.append(
                        full_method
                    )

            for role in (
                occurrence.get(
                    "class_roles",
                    []
                )
            ):

                if role not in roles:
                    roles.append(
                        role
                    )

            usage_type = (
                occurrence.get(
                    "usage_type"
                )
            )

            if (
                usage_type
                and usage_type
                not in usage_types
            ):
                usage_types.append(
                    usage_type
                )

        return {
            "classes":
                classes,

            "methods":
                methods,

            "class_roles":
                roles,

            "usage_types":
                usage_types
        }

    # =========================================================
    # ONLY RELEVANT ROLES
    # =========================================================

    def _relevant_roles(
        self,
        occurrences: list[dict]
    ) -> dict:

        result = {}

        relevant_classes = set()

        for occurrence in (
            occurrences
        ):

            class_name = (
                occurrence.get(
                    "class_name"
                )
            )

            if class_name:
                relevant_classes.add(
                    class_name
                )

        for class_name in sorted(
            relevant_classes
        ):

            roles = (
                self.class_roles.get(
                    class_name
                )
            )

            if not roles:
                continue

            result[
                class_name
            ] = sorted(
                roles
            )

        return result