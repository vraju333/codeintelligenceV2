from __future__ import annotations

import re
from pathlib import Path

from config import settings
from services.flow.endpoint_flow_service import EndpointFlowService


class JavaSampleDataService:
    """Generate scenario test data from the selected Spring endpoint contract.

    The generator intentionally preserves the Java API's JSON field names. It
    understands Java records, normal DTO fields, enums, nested DTOs and common
    collection types. It does not import or execute the analyzed application.
    """

    MAX_DEPTH = 5
    SIMPLE_TYPES = {
        "String", "CharSequence", "Character", "char", "UUID",
        "Integer", "int", "Long", "long", "Short", "short", "Byte", "byte",
        "Double", "double", "Float", "float", "BigDecimal", "BigInteger",
        "Boolean", "boolean", "LocalDate", "LocalDateTime", "OffsetDateTime",
        "ZonedDateTime", "Instant",
    }

    def __init__(self):
        raw = settings.JAVA_PROJECT_PATH
        if not raw:
            raise RuntimeError("JAVA_PROJECT_PATH is not configured")
        self.project_path = Path(raw).expanduser().resolve()
        self.endpoint_service = EndpointFlowService()
        self._type_files: dict[str, Path] | None = None

    def generate(self, http_method: str, endpoint: str) -> dict:
        method = (http_method or "").upper().strip()
        selected = self._find_endpoint(method, endpoint)
        source_path = Path(selected["file_path"])
        source = source_path.read_text(encoding="utf-8", errors="ignore")

        method_info = self._method_contract(source, selected["method_name"])
        request_type = method_info.get("request_type")
        response_type = method_info.get("response_type")

        request_json = self._sample_for_type(request_type, depth=0, field_name="request") if request_type else None
        response_json = None
        if response_type and response_type not in {"void", "Void", "ResponseEntity<Void>"}:
            response_json = self._sample_for_type(response_type, depth=0, field_name="response")

        return {
            "language": "JAVA",
            "http_method": method,
            "endpoint": selected["endpoint"],
            "controller": selected["class_name"],
            "handler": selected["method_name"],
            "request_model": request_type,
            "response_model": response_type,
            "request_json": request_json,
            "expected_response_json": response_json,
            "expected_db_effect": self._db_effect(method, request_type),
        }

    def _find_endpoint(self, method: str, endpoint: str) -> dict:
        for item in self.endpoint_service.discover_endpoints():
            if item["http_method"] == method and self.endpoint_service._paths_match(item["endpoint"], endpoint):
                return item
        raise RuntimeError(f"No Spring endpoint found for {method} {endpoint}")

    def _method_contract(self, source: str, method_name: str) -> dict:
        # Spring controller methods in the analyzed projects use standard Java
        # method declarations. Capture the return type + complete parameter list.
        pattern = re.compile(
            rf"(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?"
            rf"(?P<return>[\w.$<>?,\[\] ]+)\s+{re.escape(method_name)}\s*\((?P<params>.*?)\)\s*"
            rf"(?:throws\s+[^{{]+)?\{{",
            re.DOTALL,
        )
        match = pattern.search(source)
        if not match:
            return {}

        request_type = None
        for raw_param in self._split_top_level(match.group("params")):
            if "@RequestBody" not in raw_param:
                continue
            cleaned = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", raw_param)
            cleaned = re.sub(r"\bfinal\b", " ", cleaned)
            tokens = [token for token in cleaned.replace("\n", " ").split() if token]
            if len(tokens) >= 2:
                request_type = tokens[-2]
                break

        response_type = " ".join(match.group("return").split())
        response_type = self._unwrap_response_type(response_type)
        return {"request_type": request_type, "response_type": response_type}

    def _sample_for_type(self, raw_type: str | None, depth: int, field_name: str = "value"):
        if not raw_type or depth > self.MAX_DEPTH:
            return None
        raw_type = self._clean_type(raw_type)

        # Optional<T>
        optional_inner = self._generic_inner(raw_type, "Optional")
        if optional_inner:
            return self._sample_for_type(optional_inner, depth + 1, field_name)

        # List<T>, Set<T>, Collection<T>, Iterable<T>
        for container in ("List", "Set", "Collection", "Iterable"):
            inner = self._generic_inner(raw_type, container)
            if inner:
                sample = self._sample_for_type(inner, depth + 1, field_name)
                return [] if sample is None else [sample]

        # Map<K,V>
        map_inner = self._generic_inner(raw_type, "Map")
        if map_inner:
            parts = self._split_top_level(map_inner)
            value_type = parts[-1] if parts else "String"
            return {"key": self._sample_for_type(value_type, depth + 1, field_name)}

        base = self._base_type(raw_type)
        if base in self.SIMPLE_TYPES:
            return self._sample_scalar(base, field_name)

        type_file = self._find_type_file(base)
        if not type_file:
            return self._sample_scalar(base, field_name)

        source = type_file.read_text(encoding="utf-8", errors="ignore")
        if re.search(rf"\benum\s+{re.escape(base)}\b", source):
            values = self._enum_values(source, base)
            return values[0] if values else "SAMPLE"

        fields = self._record_fields(source, base)
        if not fields:
            fields = self._class_fields(source, base)

        result = {}
        for field_type, field_name_value, json_name in fields:
            key = json_name or field_name_value
            result[key] = self._sample_for_type(field_type, depth + 1, field_name_value)
        return result or self._sample_scalar(base, field_name)

    def _record_fields(self, source: str, type_name: str) -> list[tuple[str, str, str | None]]:
        match = re.search(rf"\brecord\s+{re.escape(type_name)}\s*\((.*?)\)\s*\{{", source, re.DOTALL)
        if not match:
            # compact records can end with "{}"
            match = re.search(rf"\brecord\s+{re.escape(type_name)}\s*\((.*?)\)\s*\{{?", source, re.DOTALL)
        if not match:
            return []
        result = []
        for component in self._split_top_level(match.group(1)):
            json_name = self._json_property_name(component)
            cleaned = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", component)
            cleaned = re.sub(r"\bfinal\b", " ", cleaned)
            tokens = [token for token in cleaned.replace("\n", " ").split() if token]
            if len(tokens) >= 2:
                result.append((" ".join(tokens[:-1]), tokens[-1], json_name))
        return result

    def _class_fields(self, source: str, type_name: str) -> list[tuple[str, str, str | None]]:
        body = self._class_body(source, type_name)
        if body is None:
            return []
        result = []
        pattern = re.compile(
            r"(?P<annotations>(?:\s*@[\w.]+(?:\([^)]*\))?\s*)*)"
            r"(?:private|protected|public)\s+(?:final\s+)?"
            r"(?P<type>[\w.$<>?,\[\] ]+)\s+(?P<name>\w+)\s*(?:=[^;]*)?;",
            re.MULTILINE,
        )
        for match in pattern.finditer(body):
            result.append((
                " ".join(match.group("type").split()),
                match.group("name"),
                self._json_property_name(match.group("annotations") or ""),
            ))
        return result

    def _class_body(self, source: str, type_name: str) -> str | None:
        match = re.search(rf"\b(?:class|record)\s+{re.escape(type_name)}\b[^{{]*\{{", source)
        if not match:
            return None
        start = match.end()
        depth = 1
        i = start
        while i < len(source):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:i]
            i += 1
        return source[start:]

    def _type_index(self) -> dict[str, Path]:
        if self._type_files is None:
            index = {}
            for path in self.project_path.rglob("*.java"):
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for match in re.finditer(r"\b(?:class|record|enum|interface)\s+(\w+)", text):
                    index.setdefault(match.group(1), path)
            self._type_files = index
        return self._type_files

    def _find_type_file(self, type_name: str) -> Path | None:
        return self._type_index().get(self._base_type(type_name))

    @staticmethod
    def _enum_values(source: str, type_name: str) -> list[str]:
        match = re.search(rf"\benum\s+{re.escape(type_name)}\b[^{{]*\{{(.*?)\}}", source, re.DOTALL)
        if not match:
            return []
        head = match.group(1).split(";", 1)[0]
        values = []
        for token in JavaSampleDataService._split_top_level(head):
            name_match = re.match(r"\s*([A-Z][A-Z0-9_]*)\b", token)
            if name_match:
                values.append(name_match.group(1))
        return values

    @staticmethod
    def _json_property_name(text: str) -> str | None:
        match = re.search(r'@JsonProperty\s*\(\s*"([^"]+)"\s*\)', text)
        return match.group(1) if match else None

    def _sample_scalar(self, type_name: str, field_name: str):
        lower = (field_name or "").lower()
        base = self._base_type(type_name)
        if base in {"Boolean", "boolean"}:
            return True
        if base in {"Integer", "int", "Long", "long", "Short", "short", "Byte", "byte", "BigInteger"}:
            if "age" in lower:
                return 25
            if "year" in lower:
                return 2
            return 1
        if base in {"Double", "double", "Float", "float", "BigDecimal"}:
            if "gpa" in lower:
                return 8.2
            if "salary" in lower:
                return 75000.0
            return 1.0
        if base == "LocalDate":
            return "2026-09-11"
        if base in {"LocalDateTime", "OffsetDateTime", "ZonedDateTime", "Instant"}:
            return "2026-09-11T10:00:00Z"
        if "email" in lower:
            return "user@example.com"
        if any(token in lower for token in ("contact", "phone", "mobile")):
            return "9876543210"
        if "name" in lower:
            return "Sample User"
        if "address" in lower or "line1" in lower:
            return "10 Main Road"
        if "city" in lower:
            return "Hyderabad"
        if "state" in lower:
            return "Telangana"
        if "country" in lower:
            return "India"
        if "postal" in lower or "zip" in lower or "pin" in lower:
            return "500081"
        if "department" in lower:
            return "Engineering"
        if "designation" in lower:
            return "Software Engineer"
        if "course" in lower:
            return "Computer Science"
        if "code" in lower or "number" in lower:
            return "SAMPLE001"
        return "sample"

    @staticmethod
    def _clean_type(value: str) -> str:
        value = re.sub(r"@[\w.]+(?:\([^)]*\))?", "", value or "")
        value = value.replace("? extends ", "").replace("? super ", "")
        return " ".join(value.split()).strip()

    @staticmethod
    def _base_type(value: str) -> str:
        value = (value or "").strip()
        if "<" in value:
            value = value.split("<", 1)[0]
        value = value.replace("[]", "").strip()
        return value.split(".")[-1]

    @staticmethod
    def _generic_inner(value: str, outer: str) -> str | None:
        match = re.match(rf"(?:[\w.]+\.)?{re.escape(outer)}\s*<(.*)>$", value.strip(), re.DOTALL)
        return match.group(1).strip() if match else None

    @staticmethod
    def _split_top_level(value: str) -> list[str]:
        parts, current = [], []
        angle = paren = bracket = brace = 0
        in_string = False
        escape = False
        for char in value or "":
            if in_string:
                current.append(char)
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                current.append(char)
                continue
            if char == "<": angle += 1
            elif char == ">" and angle: angle -= 1
            elif char == "(": paren += 1
            elif char == ")" and paren: paren -= 1
            elif char == "[": bracket += 1
            elif char == "]" and bracket: bracket -= 1
            elif char == "{": brace += 1
            elif char == "}" and brace: brace -= 1
            if char == "," and angle == paren == bracket == brace == 0:
                item = "".join(current).strip()
                if item: parts.append(item)
                current = []
            else:
                current.append(char)
        item = "".join(current).strip()
        if item: parts.append(item)
        return parts

    @staticmethod
    def _unwrap_response_type(value: str) -> str:
        value = value.strip()
        match = re.match(r"ResponseEntity\s*<(.*)>$", value)
        return match.group(1).strip() if match else value

    @staticmethod
    def _db_effect(method: str, request_type: str | None) -> str | None:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if method == "DELETE":
            return "Selected resource is deleted from persistence."
        if request_type:
            return f"Persist fields supplied by {request_type}."
        return "Persist changes performed by the selected operation."
