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
    METHOD = re.compile(
        r"(?:public|protected|private)?\s*(?:static\s+)?(?:void|[\w<>\[\], ?]+)\s+"
        r"([A-Za-z_]\w*)\s*\([^)]*\)\s*(?:throws[^{]+)?\{"
    )
    CALL = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(")
    GETSET = re.compile(r"\b(?:get|set|is)([A-Z][A-Za-z0-9_]*)\s*\(")
    ASSERTION = re.compile(r"\b(assert\w+|verify|expect|then)\s*\(", re.I)
    STRING_KEY = re.compile(r'["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*[:=,)]')
    VARIABLE_DECL = re.compile(
        r"\b([A-Z][A-Za-z0-9_]*(?:<[^;=()]+>)?)\s+([a-zA-Z_]\w*)\s*(?:=|;|,|\))"
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
        flow_methods = {
            str(x.get("changed_symbol") or "")
            for x in (scenario.get("dependency_paths") or [])
            if "." in str(x.get("changed_symbol") or "")
        }
        flow_methods.update(scenario.get("matched_methods") or [])
        classes = set(scenario.get("matched_classes") or [])
        attrs = {self._norm(x) for x in (scenario.get("changed_attributes") or []) if x}

        result = []
        for test in analysis.get("tests", []):
            calls = set(test.get("production_calls") or [])
            call_classes = {c.split(".", 1)[0] for c in calls if "." in c}
            test_attrs = {self._norm(x) for x in (test.get("attributes") or [])}
            matched_calls = sorted(calls & flow_methods)
            matched_classes = sorted(call_classes & classes)
            matched_attrs = sorted(x for x in test_attrs if x and x in attrs)

            # Automated test evidence must intersect the executable production
            # flow for THIS scenario. A shared attribute/class is useful as a
            # search hint, but by itself is not proof that this JUnit exercises
            # the affected scenario.
            #
            # Example: a GPA promotion-controller test must not count as
            # UPDATE_DATA evidence merely because both scenarios mention GPA.
            if matched_calls:
                result.append({
                    "test_class": test["test_class"],
                    "test_method": test["test_method"],
                    "file": test["file"],
                    "matched_calls": matched_calls,
                    "matched_classes": matched_classes,
                    "matched_attributes": matched_attrs,
                    "assertion_count": test["assertion_count"],
                })
        return result[:30]

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
        for match in self.METHOD.finditer(text):
            prefix = text[max(0, match.start() - 240):match.start()]
            if not self.TEST_ANNOTATION.search(prefix):
                continue
            body = self._balanced_body(text, match.end() - 1)

            variable_types = {}
            for declared_type, variable_name in self.VARIABLE_DECL.findall(body):
                variable_types[variable_name] = declared_type.split("<", 1)[0].strip()

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
