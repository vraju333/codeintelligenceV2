from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from services.knowledge.file_text_extractor import FileTextExtractor
from services.knowledge.knowledge_ingestion_service import KnowledgeIngestionService


class StructuredKnowledgeIngestionService:
    """Adapters for enterprise engineering artifacts before the common KB pipeline.

    Every adapter converts its source into deterministic, human-readable canonical
    text. KnowledgeIngestionService remains the single persistence/RAG/Neo4j path.
    """

    def __init__(self):
        self.knowledge = KnowledgeIngestionService()
        self.files = FileTextExtractor()

    def ingest_test_report(self, db: Session, filename: str, content: bytes,
                           title: str | None = None, source_ref: str | None = None) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix == ".xml":
            text, metadata = self._junit_xml(content)
        elif suffix == ".json":
            text, metadata = self._json_test_report(content)
        else:
            raise ValueError("Test report must be JUnit/Surefire XML or JSON")
        return self.knowledge.ingest(
            db, source_type="TEST_REPORT", title=title or Path(filename).stem,
            content=text, source_ref=source_ref or filename,
            metadata={"artifact_type": "TEST_REPORT", "file_name": filename, **metadata},
        )

    def ingest_openapi(self, db: Session, filename: str, content: bytes,
                       title: str | None = None, source_ref: str | None = None) -> dict:
        spec = self._load_openapi(filename, content)
        info = spec.get("info") or {}
        lines = [
            f"API: {info.get('title') or title or Path(filename).stem}",
            f"API Version: {info.get('version')}" if info.get("version") else None,
        ]

        # Preserve schemas and properties as first-class engineering knowledge.
        # The common entity extractor understands explicit Class:/Attribute: labels,
        # so these canonical lines feed both RAG and Neo4j through the existing path.
        schemas = ((spec.get("components") or {}).get("schemas") or {})
        schema_names: list[str] = []
        schema_attributes: dict[str, list[str]] = {}
        for schema_name, schema in schemas.items():
            if not isinstance(schema, dict):
                continue
            schema_names.append(str(schema_name))
            lines.append(f"Class: {schema_name}")
            properties = schema.get("properties") or {}
            attrs: list[str] = []
            if isinstance(properties, dict):
                for attribute_name in properties.keys():
                    attribute_name = str(attribute_name)
                    attrs.append(attribute_name)
                    lines.append(f"Attribute: {attribute_name}")
            schema_attributes[str(schema_name)] = attrs

        operations = 0
        operation_schemas: list[dict[str, Any]] = []
        for route, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                op = path_item.get(method)
                if not isinstance(op, dict):
                    continue
                operations += 1
                request_schemas = self._operation_request_schemas(op)
                response_schemas = self._operation_response_schemas(op)
                operation_schemas.append({
                    "http_method": method.upper(),
                    "endpoint": route,
                    "operation_id": op.get("operationId"),
                    "request_schemas": request_schemas,
                    "response_schemas": response_schemas,
                })
                lines.extend([
                    f"HTTP Method: {method.upper()}",
                    f"Endpoint: {route}",
                    f"Operation: {op.get('operationId')}" if op.get("operationId") else None,
                    f"Summary: {op.get('summary')}" if op.get("summary") else None,
                ])
                for schema_name in request_schemas:
                    lines.append(f"Request Schema: {schema_name}")
                for schema_name in response_schemas:
                    lines.append(f"Response Schema: {schema_name}")

        if not operations:
            raise ValueError("No HTTP operations found under OpenAPI paths")
        text = "\n".join(x for x in lines if x)
        return self.knowledge.ingest(
            db, source_type="API", title=title or info.get("title") or Path(filename).stem,
            content=text, source_ref=source_ref or filename,
            metadata={
                "artifact_type": "OPENAPI",
                "file_name": filename,
                "openapi_version": spec.get("openapi") or spec.get("swagger"),
                "operation_count": operations,
                "schema_names": schema_names,
                "schema_attributes": schema_attributes,
                "operation_schemas": operation_schemas,
            },
        )

    @staticmethod
    def _schema_refs(value: Any) -> list[str]:
        """Collect local OpenAPI schema names from a schema/media object."""
        found: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                    name = ref.rsplit("/", 1)[-1]
                    if name and name not in found:
                        found.append(name)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return found

    @classmethod
    def _operation_request_schemas(cls, op: dict[str, Any]) -> list[str]:
        return cls._schema_refs(op.get("requestBody") or {})

    @classmethod
    def _operation_response_schemas(cls, op: dict[str, Any]) -> list[str]:
        return cls._schema_refs(op.get("responses") or {})

    def ingest_document(self, db: Session, source_type: str, filename: str, content: bytes,
                        title: str | None = None, source_ref: str | None = None) -> dict:
        source_type = source_type.upper()
        if source_type not in {"RELEASE", "ARCHITECTURE"}:
            raise ValueError("Structured document type must be RELEASE or ARCHITECTURE")
        text = self.files.extract(filename, content)
        return self.knowledge.ingest(
            db, source_type=source_type, title=title or Path(filename).stem,
            content=text, source_ref=source_ref or filename,
            metadata={"artifact_type": source_type, "file_name": filename},
        )

    @staticmethod
    def _junit_xml(content: bytes) -> tuple[str, dict[str, Any]]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid JUnit XML: {exc}") from exc
        suites = [root] if root.tag.endswith("testsuite") else [x for x in root.iter() if x.tag.endswith("testsuite")]
        cases = [x for x in root.iter() if x.tag.endswith("testcase")]
        lines = ["Test Report: JUnit/Surefire"]
        passed = failed = skipped = 0
        test_classes: list[str] = []
        qualified_test_classes: list[str] = []

        for case in cases:
            name = case.attrib.get("name") or "unnamed"
            qualified_classname = (case.attrib.get("classname") or "").strip()
            # CommonKnowledgeEntityExtractor treats a dot as sentence punctuation
            # for explicit Class: labels. Feeding a fully-qualified Java class such
            # as com.example.FooTest therefore produced the incorrect entity `com`.
            # Keep the FQCN as metadata/evidence, but expose the Java simple name to
            # the common entity pipeline.
            simple_classname = qualified_classname.rsplit(".", 1)[-1] if qualified_classname else ""

            if simple_classname and simple_classname not in test_classes:
                test_classes.append(simple_classname)
            if qualified_classname and qualified_classname not in qualified_test_classes:
                qualified_test_classes.append(qualified_classname)

            failure = next((x for x in case if x.tag.endswith("failure") or x.tag.endswith("error")), None)
            skip = next((x for x in case if x.tag.endswith("skipped")), None)
            status = "FAIL" if failure is not None else "SKIPPED" if skip is not None else "PASS"
            failed += status == "FAIL"
            skipped += status == "SKIPPED"
            passed += status == "PASS"

            lines.append(f"Test: {name}")
            if simple_classname:
                lines.append(f"Class: {simple_classname}")
            if qualified_classname:
                lines.append(f"Qualified Test Class {qualified_classname}")
            lines.append(f"Result: {status}")
            if failure is not None:
                msg = (failure.attrib.get("message") or (failure.text or "")).strip().replace("\n", " ")
                if msg:
                    lines.append(f"Failure: {msg[:1000]}")

        return "\n".join(lines), {
            "format": "JUNIT_XML",
            "suite_count": len(suites),
            "test_count": len(cases),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "test_classes": test_classes,
            "qualified_test_classes": qualified_test_classes,
        }

    @staticmethod
    def _json_test_report(content: bytes) -> tuple[str, dict[str, Any]]:
        try:
            obj = json.loads(content.decode("utf-8-sig"))
        except Exception as exc:
            raise ValueError(f"Invalid JSON test report: {exc}") from exc
        pretty = json.dumps(obj, ensure_ascii=False, indent=2)
        return "Test Report: JSON\n" + pretty, {"format": "JSON"}

    @staticmethod
    def _load_openapi(filename: str, content: bytes) -> dict[str, Any]:
        suffix = Path(filename).suffix.lower()
        raw = content.decode("utf-8-sig")
        try:
            if suffix == ".json":
                spec = json.loads(raw)
            elif suffix in {".yaml", ".yml"}:
                try:
                    import yaml
                except ImportError as exc:
                    raise ValueError("OpenAPI YAML support requires PyYAML") from exc
                spec = yaml.safe_load(raw)
            else:
                raise ValueError("OpenAPI file must be .json, .yaml or .yml")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid OpenAPI document: {exc}") from exc
        if not isinstance(spec, dict) or not (spec.get("openapi") or spec.get("swagger")):
            raise ValueError("Document is not an OpenAPI/Swagger specification")
        return spec
