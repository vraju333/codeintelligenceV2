import re
from pathlib import Path

from config import settings


class CodeFlowService:

    def __init__(self):
        self.project_path = settings.JAVA_PROJECT_PATH

        if not self.project_path:
            raise RuntimeError(
                "JAVA_PROJECT_PATH is not configured"
            )

        self.root = Path(self.project_path)

        self.class_files: dict[str, Path] = {}
        self.class_contents: dict[str, str] = {}
        self.class_fields: dict[str, dict[str, str]] = {}
        self.repository_classes: set[str] = set()
        self.parent_classes: dict[str, str] = {}
        self.interface_implementations: dict[str, list[str]] = {}

        self._load_project()

    def _load_project(self):

        java_files = list(
            self.root.rglob("*.java")
        )

        for java_file in java_files:

            content = java_file.read_text(
                encoding="utf-8",
                errors="ignore"
            )

            class_name = self._extract_class_name(
                content
            )

            if not class_name:
                continue

            self.class_files[class_name] = java_file
            self.class_contents[class_name] = content

            self.class_fields[class_name] = (
                self._extract_fields(content)
            )

            parent = self._extract_parent_class(
                content,
                class_name
            )
            if parent:
                self.parent_classes[class_name] = parent

            for interface_name in self._extract_interfaces(
                content,
                class_name
            ):
                self.interface_implementations.setdefault(
                    interface_name,
                    []
                ).append(class_name)

            if self._is_repository(content):
                self.repository_classes.add(
                    class_name
                )

    def analyze(
        self,
        class_name: str,
        method_name: str
    ) -> dict:

        if class_name not in self.class_contents:
            raise RuntimeError(
                f"Class not found: {class_name}"
            )

        visited = set()

        flow = self._trace_method(
            class_name=class_name,
            method_name=method_name,
            visited=visited,
            depth=0
        )

        simplified_flow = (
            self._build_simplified_flow(flow)
        )

        return {
            "start_class": class_name,
            "start_method": method_name,
            "flow": flow,
            "simplified_flow": simplified_flow
        }

    def _trace_method(
        self,
        class_name: str,
        method_name: str,
        visited: set,
        depth: int
    ) -> dict:

        key = f"{class_name}.{method_name}"

        if key in visited:
            return {
                "class_name": class_name,
                "method_name": method_name,
                "recursive": True,
                **self._method_metadata(class_name, method_name),
                "calls": []
            }

        if depth > 10:
            return {
                "class_name": class_name,
                "method_name": method_name,
                "max_depth_reached": True,
                **self._method_metadata(class_name, method_name),
                "calls": []
            }

        if (
            class_name in self.repository_classes
            and self._is_repository_operation(
                method_name
            )
        ):
            repository_metadata = self._repository_method_metadata(method_name)
            return {
                "class_name": class_name,
                "method_name": method_name,
                "found": True,
                "type": "REPOSITORY",
                "framework": "SPRING_DATA_JPA",
                "operation": self._repository_operation_type(
                    method_name
                ),
                **repository_metadata,
                "file_path": str(self.class_files.get(class_name)) if self.class_files.get(class_name) else None,
                "calls": []
            }

        visited.add(key)

        content = self.class_contents.get(
            class_name
        )

        if not content:
            return {
                "class_name": class_name,
                "method_name": method_name,
                "found": False,
                **self._method_metadata(class_name, method_name),
                "calls": []
            }

        owner_class, method_body = (
            self._extract_method_body_from_hierarchy(
                class_name,
                method_name
            )
        )

        # Interface/abstract dispatch: when the declared type has no body,
        # continue into every concrete implementation that provides it.
        if not method_body:
            implementation_calls = []

            for implementation in (
                self._implementation_candidates(
                    class_name,
                    method_name
                )
            ):
                implementation_calls.append(
                    self._trace_method(
                        class_name=implementation,
                        method_name=method_name,
                        visited=visited,
                        depth=depth + 1
                    )
                )

            if implementation_calls:
                return {
                    "class_name": class_name,
                    "method_name": method_name,
                    "found": True,
                    "type": "INTERFACE_DISPATCH",
                    **self._method_metadata(class_name, method_name),
                    "calls": implementation_calls
                }

            return {
                "class_name": class_name,
                "method_name": method_name,
                "found": False,
                **self._method_metadata(class_name, method_name),
                "calls": []
            }

        trace_class = owner_class or class_name

        detected_calls = self._extract_calls(
            trace_class,
            method_body
        )

        child_calls = []

        for target_class, target_method in detected_calls:

            if (
                target_class in self.repository_classes
                and self._is_repository_operation(
                    target_method
                )
            ):
                child_calls.append(
                    {
                        "class_name": target_class,
                        "method_name": target_method,
                        "found": True,
                        "type": "REPOSITORY",
                        "framework": "SPRING_DATA_JPA",
                        "operation": (
                            self._repository_operation_type(
                                target_method
                            )
                        ),
                        **self._repository_method_metadata(target_method),
                        "file_path": str(self.class_files.get(target_class)) if self.class_files.get(target_class) else None,
                        "calls": []
                    }
                )

                continue

            if target_class not in self.class_contents:
                child_calls.append(
                    {
                        "class_name": target_class,
                        "method_name": target_method,
                        "external": True,
                        "input_parameters": [],
                        "return_type": "External/library method",
                        "file_path": None,
                        "calls": []
                    }
                )

                continue

            child = self._trace_method(
                class_name=target_class,
                method_name=target_method,
                visited=visited,
                depth=depth + 1
            )

            child_calls.append(child)

        return {
            "class_name": trace_class,
            "declared_class_name": (
                class_name
                if trace_class != class_name
                else None
            ),
            "method_name": method_name,
            "found": True,
            **self._method_metadata(trace_class, method_name),
            "calls": child_calls
        }

    def _extract_class_name(
        self,
        content: str
    ) -> str | None:

        match = re.search(
            r"\b(class|interface|enum|record)\s+(\w+)",
            content
        )

        if match:
            return match.group(2)

        return None

    def _extract_parent_class(
        self,
        content: str,
        class_name: str
    ) -> str | None:

        match = re.search(
            rf"\bclass\s+{re.escape(class_name)}(?:\s+extends\s+(\w+))?",
            content
        )

        if not match:
            return None

        return match.group(1)

    def _extract_interfaces(
        self,
        content: str,
        class_name: str
    ) -> list[str]:

        match = re.search(
            rf"\bclass\s+{re.escape(class_name)}[^{{]*?\bimplements\s+([^{{]+)",
            content,
            re.DOTALL
        )

        if not match:
            return []

        interface_block = match.group(1)
        return [
            item.strip().split("<", 1)[0].strip()
            for item in interface_block.split(",")
            if item.strip()
        ]

    def _extract_method_body_from_hierarchy(
        self,
        class_name: str,
        method_name: str
    ) -> tuple[str | None, str | None]:

        current = class_name
        seen = set()

        while current and current not in seen:
            seen.add(current)

            content = self.class_contents.get(current)
            if content:
                body = self._extract_method_body(
                    content,
                    method_name
                )
                if body:
                    return current, body

            current = self.parent_classes.get(current)

        return None, None

    def _implementation_candidates(
        self,
        class_name: str,
        method_name: str
    ) -> list[str]:

        candidates = []

        for implementation in self.interface_implementations.get(
            class_name,
            []
        ):
            owner, body = self._extract_method_body_from_hierarchy(
                implementation,
                method_name
            )
            if body and implementation not in candidates:
                candidates.append(implementation)

        # Also support abstract/base-class dispatch.
        for candidate, parent in self.parent_classes.items():
            current = parent
            seen = set()
            inherits = False

            while current and current not in seen:
                seen.add(current)
                if current == class_name:
                    inherits = True
                    break
                current = self.parent_classes.get(current)

            if not inherits:
                continue

            owner, body = self._extract_method_body_from_hierarchy(
                candidate,
                method_name
            )
            if body and candidate not in candidates:
                candidates.append(candidate)

        return candidates

    def _fields_for_class(
        self,
        class_name: str
    ) -> dict[str, str]:

        chain = []
        current = class_name
        seen = set()

        while current and current not in seen:
            seen.add(current)
            chain.append(current)
            current = self.parent_classes.get(current)

        fields = {}
        for item in reversed(chain):
            fields.update(
                self.class_fields.get(item, {})
            )

        return fields

    def _extract_fields(
        self,
        content: str
    ) -> dict[str, str]:

        fields = {}

        pattern = re.compile(
            r"""
            private
            \s+
            (?:final\s+)?
            (?P<type>[\w<>?,\s]+)
            \s+
            (?P<name>\w+)
            \s*;
            """,
            re.VERBOSE
        )

        for match in pattern.finditer(content):

            field_type = match.group(
                "type"
            ).strip()

            field_name = match.group(
                "name"
            ).strip()

            fields[field_name] = field_type

        return fields

    def _method_metadata(
        self,
        class_name: str,
        method_name: str
    ) -> dict:
        """Return Java signature metadata for a method used by the flow UI.

        Prefer the exact declaration on the requested class, then walk its
        parent hierarchy.  For interfaces/abstract methods this still works
        even when there is no method body.
        """
        current = class_name
        seen = set()

        while current and current not in seen:
            seen.add(current)
            content = self.class_contents.get(current)
            if content:
                signature = self._extract_method_signature(
                    content,
                    method_name
                )
                if signature:
                    file_path = self.class_files.get(current)
                    return {
                        **signature,
                        "file_path": str(file_path) if file_path else None,
                        "signature_owner": current,
                    }
            current = self.parent_classes.get(current)

        # Interface declarations may be the only declaration visible from
        # the declared type.  Check implementations as a safe fallback.
        for implementation in self.interface_implementations.get(class_name, []):
            content = self.class_contents.get(implementation)
            if not content:
                continue
            signature = self._extract_method_signature(content, method_name)
            if signature:
                file_path = self.class_files.get(implementation)
                return {
                    **signature,
                    "file_path": str(file_path) if file_path else None,
                    "signature_owner": implementation,
                }

        return {
            "input_parameters": [],
            "return_type": None,
            "file_path": str(self.class_files.get(class_name)) if self.class_files.get(class_name) else None,
            "signature_owner": class_name,
        }

    def _extract_method_signature(
        self,
        content: str,
        method_name: str
    ) -> dict | None:
        # Handles normal methods as well as interface/abstract declarations.
        # Annotations are ignored because we search directly for the Java
        # declaration that owns the requested method name.
        pattern = re.compile(
            rf"""
            (?:(?:public|protected|private|abstract|default|static|final|synchronized)\s+)*
            (?:<[^>]+>\s+)?
            (?P<return_type>[\w.$<>\[\],?\s]+?)
            \s+
            {re.escape(method_name)}
            \s*\(
                (?P<params>[^)]*)
            \)
            \s*(?:throws\s+[^{{;]+)?
            (?=[{{;])
            """,
            re.VERBOSE | re.MULTILINE
        )

        for match in pattern.finditer(content):
            return_type = " ".join(match.group("return_type").split())
            return_type = self._clean_java_return_type(return_type)

            # Avoid a regex match that accidentally starts in the middle of
            # an annotation or statement.
            if not return_type or return_type.startswith("return "):
                continue

            raw_params = match.group("params").strip()
            params = self._split_java_parameters(raw_params)
            return {
                "input_parameters": params,
                "return_type": return_type,
            }

        return None


    def _clean_java_return_type(self, value: str) -> str:
        """Return only the Java return type for hover metadata.

        The signature regex can occasionally start at an access modifier and
        include declaration modifiers in the captured return type.  Those are
        Java implementation details and should not be shown as the method
        output in the flowchart UI.
        """
        cleaned = " ".join((value or "").split()).strip()
        if not cleaned:
            return ""

        # Remove declaration annotations if one was captured.
        cleaned = re.sub(r"^(?:@[\w.]+(?:\s*\([^)]*\))?\s*)+", "", cleaned).strip()

        modifiers = (
            "public", "protected", "private", "abstract", "default",
            "static", "final", "synchronized", "native", "strictfp"
        )
        modifier_pattern = r"^(?:(?:" + "|".join(modifiers) + r")\s+)+"
        cleaned = re.sub(modifier_pattern, "", cleaned).strip()

        return cleaned

    def _split_java_parameters(self, raw_params: str) -> list[dict]:
        if not raw_params:
            return []

        parts = []
        current = []
        angle = square = paren = 0
        for char in raw_params:
            if char == '<':
                angle += 1
            elif char == '>':
                angle = max(0, angle - 1)
            elif char == '[':
                square += 1
            elif char == ']':
                square = max(0, square - 1)
            elif char == '(':
                paren += 1
            elif char == ')':
                paren = max(0, paren - 1)

            if char == ',' and angle == 0 and square == 0 and paren == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(char)
        if current:
            parts.append(''.join(current).strip())

        result = []
        for part in parts:
            # Remove common parameter annotations while preserving generics.
            cleaned = re.sub(r"@[\w.]+(?:\s*\([^)]*\))?\s*", "", part).strip()
            cleaned = re.sub(r"\bfinal\s+", "", cleaned).strip()
            tokens = cleaned.rsplit(None, 1)
            if len(tokens) == 2:
                param_type, param_name = tokens
            else:
                param_type, param_name = cleaned, ""
            result.append({
                "type": param_type.strip(),
                "name": param_name.strip(),
                "display": cleaned,
            })
        return result

    def _repository_method_metadata(self, method_name: str) -> dict:
        """Useful hover text for inherited Spring Data methods."""
        lower = method_name.lower()
        if lower == "save":
            return {"input_parameters": [{"type": "Entity", "name": "entity", "display": "Entity entity"}], "return_type": "Entity"}
        if lower == "findbyid":
            return {"input_parameters": [{"type": "ID", "name": "id", "display": "ID id"}], "return_type": "Optional<Entity>"}
        if lower == "deletebyid":
            return {"input_parameters": [{"type": "ID", "name": "id", "display": "ID id"}], "return_type": "void"}
        if lower == "delete":
            return {"input_parameters": [{"type": "Entity", "name": "entity", "display": "Entity entity"}], "return_type": "void"}
        if lower.startswith("existsby"):
            return {"input_parameters": [{"type": "derived query parameter", "name": "value", "display": "derived query parameter"}], "return_type": "boolean"}
        if lower.startswith("findby"):
            return {"input_parameters": [{"type": "derived query parameter", "name": "value", "display": "derived query parameter"}], "return_type": "Entity / Optional<Entity>"}
        return {"input_parameters": [], "return_type": "Spring Data result"}

    def _extract_method_body(
        self,
        content: str,
        method_name: str
    ) -> str | None:

        pattern = re.compile(
            rf"""
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
            {re.escape(method_name)}
            \s*
            \(
                [^)]*
            \)
            \s*
            (?:throws\s+[^{{]+)?
            \{{
            """,
            re.VERBOSE | re.MULTILINE
        )

        match = pattern.search(content)

        if not match:
            return None

        opening_brace = content.find(
            "{",
            match.start()
        )

        closing_brace = (
            self._find_matching_brace(
                content,
                opening_brace
            )
        )

        if closing_brace == -1:
            return None

        return content[
            opening_brace + 1:closing_brace
        ]

    def _extract_calls(
            self,
            current_class: str,
            method_body: str
    ) -> list[tuple[str, str]]:

        fields = self._fields_for_class(
            current_class
        )

        cleaned_body = (
            self._remove_constructor_expressions(
                method_body
            )
        )

        candidates = []

        object_call_pattern = re.compile(
            r"""
            (?P<object>\w+)
            \.
            (?P<method>\w+)
            \s*
            \(
            """,
            re.VERBOSE
        )

        for match in object_call_pattern.finditer(
                cleaned_body
        ):

            object_name = match.group(
                "object"
            )

            method_name = match.group(
                "method"
            )

            target_class = None

            if object_name in fields:

                target_class = self._clean_type(
                    fields[object_name]
                )

            elif object_name == "this":

                target_class = current_class

            if not target_class:
                continue

            position = match.start()

            candidates.append(
                {
                    "class_name": target_class,
                    "method_name": method_name,
                    "position": position,
                    "statement_start":
                        self._find_statement_start(
                            cleaned_body,
                            position
                        ),
                    "depth":
                        self._parenthesis_depth(
                            cleaned_body,
                            position
                        )
                }
            )

        direct_pattern = re.compile(
            r"""
            (?<!\.)
            \b
            (?P<method>[a-zA-Z_]\w*)
            \s*
            \(
            """,
            re.VERBOSE
        )

        ignored = {
            "if",
            "for",
            "while",
            "switch",
            "catch",
            "return",
            "throw",
            "new",
            "super",
            "this",
            "synchronized",
            "try",
            "CONSTRUCTOR"
        }

        for match in direct_pattern.finditer(
                cleaned_body
        ):

            method_name = match.group(
                "method"
            )

            if method_name in ignored:
                continue

            if self._looks_like_constructor(
                    method_name
            ):
                continue

            if not self._method_exists(
                    current_class,
                    method_name
            ):
                continue

            position = match.start()

            candidates.append(
                {
                    "class_name": current_class,
                    "method_name": method_name,
                    "position": position,
                    "statement_start":
                        self._find_statement_start(
                            cleaned_body,
                            position
                        ),
                    "depth":
                        self._parenthesis_depth(
                            cleaned_body,
                            position
                        )
                }
            )

        candidates.sort(
            key=lambda item: (
                item["statement_start"],
                -item["depth"],
                item["position"]
            )
        )

        result = []
        seen = set()

        for candidate in candidates:

            call = (
                candidate["class_name"],
                candidate["method_name"]
            )

            if call in seen:
                continue

            seen.add(call)
            result.append(call)

        return result

    def _find_statement_start(
            self,
            content: str,
            position: int
    ) -> int:

        semicolon = content.rfind(
            ";",
            0,
            position
        )

        opening_brace = content.rfind(
            "{",
            0,
            position
        )

        closing_brace = content.rfind(
            "}",
            0,
            position
        )

        return max(
            semicolon,
            opening_brace,
            closing_brace
        )

    def _parenthesis_depth(
            self,
            content: str,
            position: int
    ) -> int:

        depth = 0
        in_string = False
        escape = False

        for character in content[:position]:

            if character == "\\" and not escape:
                escape = True
                continue

            if character == '"' and not escape:
                in_string = not in_string

            escape = False

            if in_string:
                continue

            if character == "(":
                depth += 1

            elif character == ")":
                depth = max(
                    0,
                    depth - 1
                )

        return depth

    def _remove_constructor_expressions(
        self,
        content: str
    ) -> str:

        return re.sub(
            r"\bnew\s+[A-Z]\w*(?:<[^>]+>)?\s*\(",
            "CONSTRUCTOR(",
            content
        )

    def _looks_like_constructor(
        self,
        method_name: str
    ) -> bool:

        if not method_name:
            return False

        return method_name[0].isupper()

    def _is_repository(
        self,
        content: str
    ) -> bool:

        patterns = [
            r"extends\s+JpaRepository",
            r"extends\s+CrudRepository",
            r"extends\s+PagingAndSortingRepository",
            r"@Repository"
        ]

        return any(
            re.search(pattern, content)
            for pattern in patterns
        )

    def _is_repository_operation(
        self,
        method_name: str
    ) -> bool:

        prefixes = (
            "save",
            "find",
            "exists",
            "delete",
            "count",
            "get",
            "read",
            "query"
        )

        return method_name.startswith(
            prefixes
        )

    def _repository_operation_type(
        self,
        method_name: str
    ) -> str:

        if method_name.startswith("save"):
            return "WRITE"

        if method_name.startswith("delete"):
            return "DELETE"

        if method_name.startswith("exists"):
            return "EXISTS"

        if method_name.startswith("count"):
            return "COUNT"

        return "READ"

    def _method_exists(
        self,
        class_name: str,
        method_name: str
    ) -> bool:

        current = class_name
        seen = set()
        pattern = re.compile(
            rf"\b{re.escape(method_name)}\s*\("
        )

        while current and current not in seen:
            seen.add(current)
            content = self.class_contents.get(current)
            if content and pattern.search(content):
                return True
            current = self.parent_classes.get(current)

        return False

    def _clean_type(
        self,
        type_name: str
    ) -> str:

        type_name = type_name.strip()

        if "<" in type_name:
            type_name = type_name.split(
                "<",
                1
            )[0]

        return type_name.strip()

    def _remove_duplicates(
        self,
        calls: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:

        result = []
        seen = set()

        for call in calls:

            if call in seen:
                continue

            seen.add(call)
            result.append(call)

        return result

    def _find_matching_brace(
        self,
        content: str,
        opening_brace: int
    ) -> int:

        depth = 0
        in_string = False
        escape = False

        for index in range(
            opening_brace,
            len(content)
        ):

            character = content[index]

            if character == "\\" and not escape:
                escape = True
                continue

            if character == '"' and not escape:
                in_string = not in_string

            escape = False

            if in_string:
                continue

            if character == "{":
                depth += 1

            elif character == "}":
                depth -= 1

                if depth == 0:
                    return index

        return -1

    def _build_simplified_flow(
        self,
        flow: dict
    ) -> list[str]:

        result = []

        self._collect_simplified_nodes(
            flow,
            result
        )

        return result

    def _collect_simplified_nodes(
        self,
        node: dict,
        result: list[str]
    ):

        class_name = node.get(
            "class_name"
        )

        method_name = node.get(
            "method_name"
        )

        if not class_name or not method_name:
            return

        current = (
            f"{class_name}.{method_name}"
        )

        if current not in result:
            result.append(current)

        calls = node.get(
            "calls",
            []
        )

        for child in calls:

            if self._include_in_simplified_flow(
                child
            ):
                self._collect_simplified_nodes(
                    child,
                    result
                )

    def _include_in_simplified_flow(
        self,
        node: dict
    ) -> bool:

        class_name = node.get(
            "class_name",
            ""
        )

        method_name = node.get(
            "method_name",
            ""
        )

        if node.get("type") == "REPOSITORY":
            return True

        if class_name.endswith(
            "Controller"
        ):
            return True

        if class_name.endswith(
            "Service"
        ):
            return True

        if class_name.endswith(
            "Mapper"
        ):
            return True

        return False