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
            "branches": self._extract_branches(trace_class, method_body),
            "data_flow": self._extract_data_flow(trace_class, method_name, method_body),
            "calls": child_calls
        }

    def _extract_data_flow(
        self,
        current_class: str,
        method_name: str,
        method_body: str
    ) -> list[dict]:
        """Extract lightweight Java value propagation across local assignments and calls.

        Example:
            double score = request.gpa();
            evaluatePromotion(score);

        produces request.gpa -> score -> evaluatePromotion.studentGpa.
        This is static evidence only; application code is never executed.
        """
        aliases: dict[str, list[str]] = {}
        evidence: list[dict] = []
        seen = set()

        def add(kind, source, target, **extra):
            source = (source or "").strip()
            target = (target or "").strip()
            if not source or not target:
                return
            key = (kind, source, target, extra.get("target_class"), extra.get("target_method"),
                   extra.get("parameter_name"), extra.get("argument_index"))
            if key in seen:
                return
            seen.add(key)
            evidence.append({
                "type": kind,
                "source": source,
                "target": target,
                **extra
            })

        # Local assignment propagation: Type x = expression; or x = expression;
        assignment_pattern = re.compile(
            r"(?:\b[\w.$<>\[\],?]+\s+)?(?P<target>[a-zA-Z_]\w*)\s*=\s*(?P<source>[^;]+);"
        )
        for match in assignment_pattern.finditer(method_body):
            target = match.group("target")
            source = " ".join(match.group("source").split())
            if source.startswith(("new ", "return ", "throw ")):
                continue
            origins = self._java_value_origins(source)
            if origins:
                aliases[target] = origins
                for origin in origins:
                    add("ASSIGNMENT", origin, target)

        # Call argument -> target method parameter propagation.
        for call in self._extract_call_arguments(current_class, method_body):
            target_class = call["class_name"]
            target_method = call["method_name"]
            params = self._method_metadata(target_class, target_method).get("input_parameters") or []
            for index, argument in enumerate(call["arguments"]):
                if index >= len(params):
                    break
                param_name = str(params[index].get("name") or "").strip()
                if not param_name:
                    continue
                origins = []
                for origin in self._java_value_origins(argument):
                    origins.extend(aliases.get(origin, [origin]))
                if not origins and argument in aliases:
                    origins = aliases[argument]
                for origin in dict.fromkeys(origins):
                    add(
                        "METHOD_ARGUMENT",
                        origin,
                        param_name,
                        target_class=target_class,
                        target_method=target_method,
                        parameter_name=param_name,
                        argument=argument.strip(),
                        argument_index=index,
                    )

        return evidence

    def _java_value_origins(self, expression: str) -> list[str]:
        """Return attribute/variable references that can carry a value."""
        result = []

        # JavaBean accessors: student.getGpa() -> gpa
        for _, name in re.findall(
            r"\b(?:\w+\.)*(get|is)([A-Z][A-Za-z0-9_]*)\s*\(", expression
        ):
            value = name[0].lower() + name[1:]
            if value not in result:
                result.append(value)

        # Record/accessor style: request.gpa() -> gpa
        for name in re.findall(r"\b\w+\.([a-z][A-Za-z0-9_]*)\s*\(", expression):
            if name not in {"equals", "isEmpty", "nonNull", "isNull"} and name not in result:
                result.append(name)

        # Simple variable used as an argument/expression.
        cleaned = expression.strip()
        if re.fullmatch(r"[a-zA-Z_]\w*", cleaned) and cleaned not in result:
            result.append(cleaned)

        return result

    def _extract_call_arguments(self, current_class: str, method_body: str) -> list[dict]:
        """Resolve project calls and retain their argument expressions."""
        fields = self._fields_for_class(current_class)
        result = []

        pattern = re.compile(
            r"(?:(?P<object>\b[a-zA-Z_]\w*)\s*\.\s*)?"
            r"(?P<method>[a-zA-Z_]\w*)\s*\("
        )
        ignored = {
            "if", "for", "while", "switch", "catch", "return", "throw",
            "new", "super", "this", "synchronized", "try"
        }

        for match in pattern.finditer(method_body):
            method = match.group("method")
            obj = match.group("object")
            if method in ignored or self._looks_like_constructor(method):
                continue

            target_class = None
            if obj == "this" or obj is None:
                if self._method_exists(current_class, method):
                    target_class = current_class
            elif obj in fields:
                target_class = self._clean_type(fields[obj])

            if not target_class:
                continue

            close = self._find_matching_delimiter(
                method_body, match.end() - 1, "(", ")"
            )
            if close == -1:
                continue

            arguments = self._split_call_arguments(
                method_body[match.end():close]
            )
            result.append({
                "class_name": target_class,
                "method_name": method,
                "arguments": arguments,
            })

        return result

    @staticmethod
    def _split_call_arguments(raw: str) -> list[str]:
        if not raw.strip():
            return []
        result, current = [], []
        paren = angle = square = brace = 0
        in_string = False
        escape = False

        for char in raw:
            if char == "\\" and not escape:
                escape = True
                current.append(char)
                continue
            if char == '"' and not escape:
                in_string = not in_string
            escape = False

            if not in_string:
                if char == "(": paren += 1
                elif char == ")": paren = max(0, paren - 1)
                elif char == "<": angle += 1
                elif char == ">": angle = max(0, angle - 1)
                elif char == "[": square += 1
                elif char == "]": square = max(0, square - 1)
                elif char == "{": brace += 1
                elif char == "}": brace = max(0, brace - 1)
                elif char == "," and paren == angle == square == brace == 0:
                    result.append("".join(current).strip())
                    current = []
                    continue
            current.append(char)

        if current:
            result.append("".join(current).strip())
        return result

    def _extract_branches(self, current_class: str, method_body: str) -> list[dict]:
        branches = []
        index = 0
        while index < len(method_body):
            match = re.search(r"\bif\s*\(", method_body[index:])
            if not match:
                break
            start = index + match.start()
            cond_open = method_body.find("(", start)
            cond_close = self._find_matching_delimiter(method_body, cond_open, "(", ")")
            if cond_close == -1:
                index = start + 2
                continue
            condition = " ".join(method_body[cond_open + 1:cond_close].split())
            body_open = self._skip_to_branch_body(method_body, cond_close + 1)
            if body_open == -1:
                index = cond_close + 1
                continue
            body_close = self._find_matching_brace(method_body, body_open)
            if body_close == -1:
                index = body_open + 1
                continue
            branches.append(self._branch_record(
                current_class, "IF", condition, method_body[body_open + 1:body_close]
            ))
            cursor = body_close + 1
            while cursor < len(method_body):
                cursor = self._skip_whitespace(method_body, cursor)
                em = re.match(r"else\b", method_body[cursor:])
                if not em:
                    break
                cursor += em.end()
                cursor = self._skip_whitespace(method_body, cursor)
                if re.match(r"if\b", method_body[cursor:]):
                    co = method_body.find("(", cursor)
                    cc = self._find_matching_delimiter(method_body, co, "(", ")")
                    if cc == -1: break
                    condition = " ".join(method_body[co + 1:cc].split())
                    bo = self._skip_to_branch_body(method_body, cc + 1)
                    if bo == -1: break
                    bc = self._find_matching_brace(method_body, bo)
                    if bc == -1: break
                    branches.append(self._branch_record(
                        current_class, "ELSE_IF", condition, method_body[bo + 1:bc]
                    ))
                    cursor = bc + 1
                    continue
                bo = self._skip_to_branch_body(method_body, cursor)
                if bo == -1: break
                bc = self._find_matching_brace(method_body, bo)
                if bc == -1: break
                branches.append(self._branch_record(
                    current_class, "ELSE", "otherwise", method_body[bo + 1:bc]
                ))
                cursor = bc + 1
                break
            index = max(cursor, body_close + 1)
        return branches

    def _branch_record(self, current_class, branch_type, condition, branch_body):
        return {
            "branch_type": branch_type,
            "condition": condition,
            "attributes": self._condition_attributes(condition),
            "calls": [
                {"class_name": cls, "method_name": method}
                for cls, method in self._extract_calls(current_class, branch_body)
            ],
        }

    def _condition_attributes(self, condition: str) -> list[str]:
        attributes = []
        # JavaBean accessor: s.getGpa() / s.isActive()
        for _, name in re.findall(
            r"\b(?:\w+\.)*(get|is)([A-Z][A-Za-z0-9_]*)\s*\(", condition
        ):
            value = name[0].lower() + name[1:]
            if value not in attributes:
                attributes.append(value)
        # Java record/accessor style: r.gpa()
        for name in re.findall(r"\b\w+\.([a-z][A-Za-z0-9_]*)\s*\(", condition):
            if name not in {"equals", "isEmpty", "nonNull", "isNull"} and name not in attributes:
                attributes.append(name)
        # Direct field access: r.gpa
        for name in re.findall(r"\b\w+\.([a-zA-Z_]\w*)\b", condition):
            if name not in {"equals", "isEmpty", "nonNull", "isNull"} and name not in attributes:
                attributes.append(name)
        return attributes

    def _find_matching_delimiter(self, content, opening, open_char, close_char):
        if opening < 0:
            return -1
        depth = 0
        in_string = False
        escape = False
        for index in range(opening, len(content)):
            char = content[index]
            if char == "\\" and not escape:
                escape = True
                continue
            if char == '"' and not escape:
                in_string = not in_string
            escape = False
            if in_string:
                continue
            if char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
                if depth == 0:
                    return index
        return -1

    @staticmethod
    def _skip_whitespace(content, index):
        while index < len(content) and content[index].isspace():
            index += 1
        return index

    def _skip_to_branch_body(self, content, index):
        index = self._skip_whitespace(content, index)
        return index if index < len(content) and content[index] == "{" else -1

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