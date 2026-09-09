import re
from pathlib import Path

from config import settings
from services.flow.code_flow_service import CodeFlowService


class EndpointFlowService:

    HTTP_ANNOTATIONS = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE"
    }

    def __init__(self):
        if not settings.JAVA_PROJECT_PATH:
            raise RuntimeError(
                "JAVA_PROJECT_PATH is not configured"
            )

        self.project_path = Path(
            settings.JAVA_PROJECT_PATH
        )

        self.code_flow_service = (
            CodeFlowService()
        )

    def analyze_endpoint(
        self,
        http_method: str,
        endpoint: str
    ) -> dict:

        http_method = http_method.upper().strip()
        endpoint = self._normalize_path(endpoint)

        endpoints = self.discover_endpoints()

        matching_endpoint = None

        for registered_endpoint in endpoints:

            if (
                registered_endpoint["http_method"]
                == http_method
                and self._paths_match(
                    registered_endpoint["endpoint"],
                    endpoint
                )
            ):
                matching_endpoint = (
                    registered_endpoint
                )
                break

        if not matching_endpoint:
            raise RuntimeError(
                f"No controller method found for "
                f"{http_method} {endpoint}"
            )

        flow_result = (
            self.code_flow_service.analyze(
                class_name=(
                    matching_endpoint[
                        "class_name"
                    ]
                ),
                method_name=(
                    matching_endpoint[
                        "method_name"
                    ]
                )
            )
        )

        return {
            "http_method": http_method,
            "endpoint": endpoint,
            "controller": {
                "class_name": (
                    matching_endpoint[
                        "class_name"
                    ]
                ),
                "method_name": (
                    matching_endpoint[
                        "method_name"
                    ]
                ),
                "file_path": (
                    matching_endpoint[
                        "file_path"
                    ]
                )
            },
            "simplified_flow": (
                flow_result[
                    "simplified_flow"
                ]
            ),
            "flow": flow_result["flow"]
        }

    def discover_endpoints(
        self
    ) -> list[dict]:

        result = []

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

            if not self._is_controller(
                content
            ):
                continue

            class_name = (
                self._extract_class_name(
                    content
                )
            )

            if not class_name:
                continue

            base_path = (
                self._extract_base_path(
                    content
                )
            )

            controller_endpoints = (
                self._extract_method_endpoints(
                    content=content,
                    class_name=class_name,
                    base_path=base_path,
                    file_path=str(java_file)
                )
            )

            result.extend(
                controller_endpoints
            )

        result.sort(
            key=lambda item: (
                item["endpoint"],
                item["http_method"]
            )
        )

        return result

    def _is_controller(
        self,
        content: str
    ) -> bool:

        return (
            "@RestController" in content
            or "@Controller" in content
        )

    def _extract_class_name(
        self,
        content: str
    ) -> str | None:

        match = re.search(
            r"\bclass\s+(\w+)",
            content
        )

        if not match:
            return None

        return match.group(1)

    def _extract_base_path(
        self,
        content: str
    ) -> str:

        class_match = re.search(
            r"\bclass\s+\w+",
            content
        )

        if not class_match:
            return ""

        header = content[
            :class_match.start()
        ]

        request_mappings = list(
            re.finditer(
                r"@RequestMapping\s*"
                r"\((?P<args>[^)]*)\)",
                header,
                re.DOTALL
            )
        )

        if not request_mappings:
            return ""

        args = (
            request_mappings[-1]
            .group("args")
        )

        path = self._extract_path_from_args(
            args
        )

        return self._normalize_path(
            path
        )

    def _extract_method_endpoints(
        self,
        content: str,
        class_name: str,
        base_path: str,
        file_path: str
    ) -> list[dict]:

        endpoints = []

        method_pattern = re.compile(
            r"""
            (?P<annotations>
                (?:
                    \s*
                    @[\w.]+
                    (?:\s*\([^)]*\))?
                    \s*
                )+
            )
            (?:
                public|
                protected|
                private
            )
            \s+
            (?:static\s+)?
            (?:final\s+)?
            (?:synchronized\s+)?
            (?:<[^>]+>\s+)?
            [\w<>\[\],.?]+\s+
            (?P<method_name>\w+)
            \s*
            \(
            """,
            re.VERBOSE | re.MULTILINE
        )

        for method_match in (
            method_pattern.finditer(
                content
            )
        ):

            annotations = (
                method_match.group(
                    "annotations"
                )
            )

            method_name = (
                method_match.group(
                    "method_name"
                )
            )

            mappings = (
                self._extract_mappings_from_annotations(
                    annotations
                )
            )

            for mapping in mappings:

                full_path = (
                    self._join_paths(
                        base_path,
                        mapping["path"]
                    )
                )

                endpoints.append(
                    {
                        "http_method":
                            mapping[
                                "http_method"
                            ],
                        "endpoint":
                            full_path,
                        "class_name":
                            class_name,
                        "method_name":
                            method_name,
                        "file_path":
                            file_path
                    }
                )

        return endpoints

    def _extract_mappings_from_annotations(
        self,
        annotations: str
    ) -> list[dict]:

        result = []

        for (
            annotation_name,
            http_method
        ) in self.HTTP_ANNOTATIONS.items():

            pattern = re.compile(
                rf"@{annotation_name}"
                r"(?:\s*\((?P<args>[^)]*)\))?"
            )

            for match in pattern.finditer(
                annotations
            ):

                args = (
                    match.group("args")
                    or ""
                )

                paths = (
                    self._extract_paths_from_args(
                        args
                    )
                )

                if not paths:
                    paths = [""]

                for path in paths:

                    result.append(
                        {
                            "http_method":
                                http_method,
                            "path": path
                        }
                    )

        request_mapping_pattern = (
            re.compile(
                r"@RequestMapping"
                r"\s*\((?P<args>[^)]*)\)",
                re.DOTALL
            )
        )

        for match in (
            request_mapping_pattern
            .finditer(annotations)
        ):

            args = (
                match.group("args")
            )

            http_methods = (
                self._extract_request_methods(
                    args
                )
            )

            paths = (
                self._extract_paths_from_args(
                    args
                )
            )

            if not paths:
                paths = [""]

            for http_method in http_methods:
                for path in paths:

                    result.append(
                        {
                            "http_method":
                                http_method,
                            "path": path
                        }
                    )

        return result

    def _extract_request_methods(
        self,
        args: str
    ) -> list[str]:

        methods = re.findall(
            r"RequestMethod\."
            r"(GET|POST|PUT|PATCH|DELETE)",
            args
        )

        return methods

    def _extract_path_from_args(
        self,
        args: str
    ) -> str:

        paths = (
            self._extract_paths_from_args(
                args
            )
        )

        if not paths:
            return ""

        return paths[0]

    def _extract_paths_from_args(
        self,
        args: str
    ) -> list[str]:

        if not args:
            return []

        named_match = re.search(
            r"(?:value|path)"
            r"\s*=\s*"
            r"(?P<value>"
            r"\{[^}]*\}"
            r"|"
            r'"[^"]*"'
            r")",
            args,
            re.DOTALL
        )

        if named_match:

            value = (
                named_match.group(
                    "value"
                )
            )

            return self._extract_strings(
                value
            )

        direct_match = re.match(
            r'\s*"([^"]*)"',
            args
        )

        if direct_match:
            return [
                direct_match.group(1)
            ]

        array_match = re.match(
            r"\s*\{([^}]*)\}",
            args,
            re.DOTALL
        )

        if array_match:
            return self._extract_strings(
                array_match.group(0)
            )

        return []

    def _extract_strings(
        self,
        value: str
    ) -> list[str]:

        return re.findall(
            r'"([^"]*)"',
            value
        )

    def _join_paths(
        self,
        base_path: str,
        method_path: str
    ) -> str:

        base_path = (
            self._normalize_path(
                base_path
            )
        )

        method_path = (
            self._normalize_path(
                method_path
            )
        )

        if base_path == "/":
            base_path = ""

        if method_path == "/":
            method_path = ""

        combined = (
            f"{base_path}"
            f"{method_path}"
        )

        return self._normalize_path(
            combined
        )

    def _normalize_path(
        self,
        path: str
    ) -> str:

        if not path:
            return "/"

        path = path.strip()

        if not path.startswith("/"):
            path = "/" + path

        path = re.sub(
            r"/+",
            "/",
            path
        )

        if (
            len(path) > 1
            and path.endswith("/")
        ):
            path = path[:-1]

        return path

    def _paths_match(
        self,
        registered_path: str,
        requested_path: str
    ) -> bool:

        registered_path = (
            self._normalize_path(
                registered_path
            )
        )

        requested_path = (
            self._normalize_path(
                requested_path
            )
        )

        pattern = re.sub(
            r"\{[^/{}]+\}",
            r"[^/]+",
            registered_path
        )

        pattern = (
            "^"
            + pattern
            + "$"
        )

        return bool(
            re.match(
                pattern,
                requested_path
            )
        )