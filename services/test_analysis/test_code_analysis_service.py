import re
from pathlib import Path
from config import settings


class TestCodeAnalysisService:
    """Lightweight, evidence-only Java test scanner.

    It does not claim runtime coverage. It reports static evidence found in
    Java test source: @Test methods, production-looking calls, attributes,
    assertions and branch-value hints.
    """

    TEST_ANNOTATION = re.compile(r"@(?:Test|ParameterizedTest|RepeatedTest)\b")
    # Start from the JUnit annotation and capture the method that follows it.
    # This prevents the generic METHOD regex from accidentally treating the
    # annotation identifier itself as part of a Java method declaration.
    TEST_METHOD = re.compile(
        r"@(?:Test|ParameterizedTest|RepeatedTest)\b"
        r"(?:\s*\([^)]*\))?"
        r"(?:\s*@[A-Za-z_][\w.]*(?:\s*\([^)]*\))?)*"
        r"\s*(?:public|protected|private)?\s*(?:static\s+)?"
        r"(?:void|[\w<>\[\], ?]+)\s+"
        r"([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws[^{]+)?\{",
        re.MULTILINE,
    )
    CALL = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(")
    GETSET = re.compile(r"\b(?:get|set|is)([A-Z][A-Za-z0-9_]*)\s*\(")
    ASSERTION = re.compile(r"\b(assert\w+|verify|expect|then)\s*\(", re.I)
    STRING_KEY = re.compile(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*[:=,)]')
    VARIABLE_DECL = re.compile(
        r"\b([A-Z][A-Za-z0-9_]*(?:<[^;=()]+>)?)\s+([a-zA-Z_]\w*)\s*(?:=|;|,|\))"
    )
    NEW_ASSIGNMENT = re.compile(
        r"\b(?:var\s+)?([a-zA-Z_]\w*)\s*=\s*new\s+([A-Z][A-Za-z0-9_]*)\s*\("
    )
    TYPED_NEW_ASSIGNMENT = re.compile(
        r"\b([A-Z][A-Za-z0-9_]*)\s+([a-zA-Z_]\w*)\s*=\s*new\s+([A-Z][A-Za-z0-9_]*)\s*\("
    )

    def analyse(self) -> dict:
        project = Path(settings.JAVA_PROJECT_PATH).resolve()
        files = self._test_files(project)
        tests = []
        for file in files:
            tests.extend(self._analyse_file(project, file))

        attributes = sorted({a for t in tests for a in t["attributes"]})
        calls = sorted({c for t in tests for c in t["production_calls"]})
        return {
            "project_path": str(project),
            "test_files": len(files),
            "test_methods": len(tests),
            "attributes_with_test_evidence": attributes,
            "production_calls_with_test_evidence": calls,
            "tests": tests,
            "evidence_type": "STATIC_TEST_SOURCE",
            "limitations": (
                "Static source evidence only; this is not JaCoCo/runtime line or branch coverage."
            ),
        }

    def evidence_for_scenario(self, scenario: dict, analysis: dict) -> list[dict]:
        """Return static JUnit evidence for one impacted scenario.

        Matching is intentionally evidence-based:
        1. the test must touch the changed business attribute when the scenario
           has changed attributes; and
        2. the test must intersect the scenario's executable production flow.

        The scenario payloads produced by Git/SDLC analysis are not all shaped
        identically, so collect flow evidence from matched_methods,
        matched_classes, dependency_paths, execution_flow and flow.
        """
        flow_methods: set[str] = set()
        flow_classes: set[str] = set()

        def add_symbol(value):
            if value is None:
                return
            if isinstance(value, dict):
                # Support the different flow payloads used by CodeIntelligence.
                for key in (
                    "changed_symbol", "method", "method_name", "symbol",
                    "class_method", "source", "target", "from", "to",
                ):
                    add_symbol(value.get(key))
                for key in (
                    "path", "steps", "nodes", "methods", "execution_flow",
                    "flow", "dependency_path",
                ):
                    add_symbol(value.get(key))
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    add_symbol(item)
                return

            symbol = str(value).strip()
            if not symbol:
                return

            # Ignore HTTP endpoint labels such as "PUT /api/persons/{id}".
            if "/" in symbol and " " in symbol:
                return

            # Normal Class.method representation.
            if "." in symbol and " " not in symbol:
                flow_methods.add(symbol)
                flow_classes.add(symbol.split(".", 1)[0])
                return

            # Class-only evidence.
            if re.fullmatch(r"[A-Za-z_]\w*", symbol):
                flow_classes.add(symbol)

        add_symbol(scenario.get("matched_methods") or [])
        add_symbol(scenario.get("matched_classes") or [])
        add_symbol(scenario.get("dependency_paths") or [])
        add_symbol(scenario.get("execution_flow") or [])
        add_symbol(scenario.get("flow") or [])

        # Scenario payloads may expose the changed attributes under different
        # keys depending on whether they came from Git traceability or impact.
        raw_attrs = (
            scenario.get("changed_attributes")
            or scenario.get("matched_attributes")
            or scenario.get("attributes")
            or []
        )
        if isinstance(raw_attrs, str):
            raw_attrs = [raw_attrs]
        attrs = {self._norm(x) for x in raw_attrs if x}

        result = []
        seen = set()

        for test in analysis.get("tests", []):
            calls = set(test.get("production_calls") or [])
            call_classes = {c.split(".", 1)[0] for c in calls if "." in c}
            test_attrs = {self._norm(x) for x in (test.get("attributes") or []) if x}

            matched_calls = sorted(calls & flow_methods)
            matched_classes = sorted(call_classes & flow_classes)
            matched_attrs = sorted(test_attrs & attrs)

            # When the scenario is attribute-driven (KAN-4 => preferredLanguage),
            # require that exact attribute. This prevents a GPA-only test from
            # becoming evidence merely because it uses Student/StudentMapper.
            attribute_ok = bool(matched_attrs) if attrs else True

            # Prefer exact Class.method intersection. If a scenario payload only
            # carries class-level flow information, class+attribute is acceptable.
            flow_ok = bool(matched_calls) or bool(matched_classes)

            if not (attribute_ok and flow_ok):
                continue

            key = (test.get("test_class"), test.get("test_method"))
            if key in seen:
                continue
            seen.add(key)

            if matched_calls and matched_attrs:
                basis = "FLOW_METHOD_AND_ATTRIBUTE_MATCH"
            elif matched_calls:
                basis = "FLOW_METHOD_MATCH"
            else:
                basis = "FLOW_CLASS_AND_ATTRIBUTE_MATCH"

            scenario_relevance = str(scenario.get("relevance") or "").upper()
            if scenario_relevance == "DIRECT":
                coverage_classification = "DIRECT"
            elif scenario_relevance == "TRANSITIVE":
                coverage_classification = "TRANSITIVE"
            else:
                coverage_classification = "SHARED"

            result.append({
                "test_class": test.get("test_class"),
                "test_method": test.get("test_method"),
                "file": test.get("file"),
                "matched_calls": matched_calls,
                "matched_classes": matched_classes,
                "matched_attributes": matched_attrs,
                "evidence_basis": basis,
                "evidence_scope": "SCENARIO_STATIC_JUNIT",
                "coverage_classification": coverage_classification,
                "assertion_count": test.get("assertion_count", 0),
                "has_assertion": bool(test.get("has_assertion")),
            })

        return result[:30]

    def evidence_for_changed_components(
        self,
        changed_classes: list[str],
        changed_attributes: list[str],
        analysis: dict,
    ) -> list[dict]:
        """Return static JUnit evidence for production components changed in Git.

        This is intentionally labelled COMPONENT evidence. It proves that a test
        invokes a changed production class/method and touches a changed business
        attribute; it does not claim endpoint/runtime coverage.
        """
        classes = {str(x or "").strip() for x in (changed_classes or []) if x}
        attrs = {self._norm(x) for x in (changed_attributes or []) if x}
        result = []
        seen = set()

        for test in analysis.get("tests", []):
            calls = set(test.get("production_calls") or [])
            matched_calls = sorted(
                c for c in calls
                if "." in c and c.split(".", 1)[0] in classes
            )
            if not matched_calls:
                continue

            test_attrs = {self._norm(x) for x in (test.get("attributes") or []) if x}
            matched_attrs = sorted(x for x in test_attrs if x and x in attrs)
            if attrs and not matched_attrs:
                continue

            key = (test.get("test_class"), test.get("test_method"), tuple(matched_calls))
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "test_class": test.get("test_class"),
                "test_method": test.get("test_method"),
                "file": test.get("file"),
                "matched_calls": matched_calls,
                "matched_classes": sorted({c.split(".", 1)[0] for c in matched_calls}),
                "matched_attributes": matched_attrs,
                "evidence_basis": "CURRENT_GIT_COMPONENT_AND_ATTRIBUTE_MATCH",
                "evidence_scope": "COMPONENT_STATIC_JUNIT",
                "assertion_count": test.get("assertion_count", 0),
            })
        return result[:50]

    def _test_files(self, project: Path) -> list[Path]:
        roots = [
            project / "src/test/java",
            project / "src/integrationTest/java",
            project / "test",
            project / "tests",
        ]
        found = []
        for base in roots:
            if base.exists():
                found.extend(base.rglob("*.java"))
        if not found:
            found = [
                p for p in project.rglob("*.java")
                if any(part.lower() in {"test", "tests"} for part in p.parts)
                or p.name.endswith(("Test.java", "Tests.java", "IT.java"))
            ]
        return sorted(set(found))

    def _analyse_file(self, project: Path, file: Path) -> list[dict]:
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        class_match = re.search(r"\bclass\s+([A-Za-z_]\w*)", text)
        test_class = class_match.group(1) if class_match else file.stem
        result = []
        for match in self.TEST_METHOD.finditer(text):
            body = self._balanced_body(text, match.end() - 1)

            variable_types = {}
            # Resolve both method-local variables and mapper/service fields declared
            # at class level. This is important for common JUnit styles such as
            # `private final AddressMapper mapper = new AddressMapper()` and
            # `var mapper = new AddressMapper()`.
            for declared_type, variable_name in self.VARIABLE_DECL.findall(text):
                variable_types[variable_name] = declared_type.split("<", 1)[0].strip()
            for variable_name, constructed_type in self.NEW_ASSIGNMENT.findall(text):
                variable_types[variable_name] = constructed_type
            for declared_type, variable_name, constructed_type in self.TYPED_NEW_ASSIGNMENT.findall(text):
                variable_types[variable_name] = constructed_type or declared_type

            calls = []
            raw_calls = []
            for owner, method in self.CALL.findall(body):
                if owner in {"Assertions", "Assert", "Mockito", "BDDMockito"}:
                    continue
                raw_value = f"{owner}.{method}"
                if raw_value not in raw_calls:
                    raw_calls.append(raw_value)
                resolved_owner = variable_types.get(owner, owner)
                value = f"{resolved_owner}.{method}"
                if value not in calls:
                    calls.append(value)
            attrs = []
            for raw in self.GETSET.findall(body):
                value = raw[:1].lower() + raw[1:]
                if value not in attrs:
                    attrs.append(value)
            for raw in self.STRING_KEY.findall(body):
                if raw not in attrs:
                    attrs.append(raw)
            result.append({
                "test_class": test_class,
                "test_method": match.group(1),
                "file": str(file.relative_to(project)).replace("\\", "/"),
                "production_calls": calls,
                "raw_calls": raw_calls,
                "resolved_variable_types": variable_types,
                "attributes": attrs,
                "assertion_count": len(self.ASSERTION.findall(body)),
                "has_assertion": bool(self.ASSERTION.search(body)),
            })
        return result

    @staticmethod
    def _balanced_body(text: str, brace: int) -> str:
        depth = 0
        quote = None
        escaped = False
        for i in range(brace, len(text)):
            ch = text[i]
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == quote:
                    quote = None
                continue
            if ch in {"'", '"'}:
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[brace + 1:i]
        return text[brace + 1:]

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())
