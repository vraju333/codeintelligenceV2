from __future__ import annotations

import re
from typing import Any


class CommonKnowledgeEntityExtractor:
    """Deterministic common-entity extraction for engineering knowledge sources.

    The extractor intentionally favors explicit engineering identifiers/labels over
    broad NLP guesses so the graph remains explainable and works offline.
    """

    MONTH_PATTERN = (
        r"\b(?:January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+20\d{2}\b"
    )

    @staticmethod
    def _unique(values: list[str], limit: int = 50) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            value = str(value or "").strip().strip("`'\" ,;:")
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                out.append(value)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _labeled_values(content: str, labels: str) -> list[str]:
        # Labels may appear at the start of a line OR inline in compact knowledge
        # text, e.g. "JIRA: STUD-101. Attribute: gpa. Scenario: STUDENT_UPDATE."
        # Stop at sentence punctuation or before the next Label: token so one
        # field never consumes the following field.
        pattern = (
            rf"(?i)(?:^|[\r\n]|(?<=[.;]))\s*(?:{labels})\s*[:=-]\s*"
            rf"(.+?)(?=\s*(?:[.;]|$|[A-Za-z][A-Za-z _-]{{1,30}}\s*[:=]))"
        )
        values: list[str] = []
        for raw in re.findall(pattern, content):
            parts = re.split(r"\s*,\s*", raw.strip())
            values.extend(p for p in parts if p)
        return values

    @staticmethod
    def _file_values(content: str) -> list[str]:
        # File paths contain dots (StudentService.java, service.py, pom.xml), so
        # they cannot use the generic labeled-value parser where a dot is also
        # treated as sentence punctuation. Stop only at a newline/semicolon, the
        # end of text, or the next explicit engineering label.
        labels = (
            r"JIRA|Attribute(?:s)?|Field(?:s)?|Property|Properties|"
            r"Scenario(?:s)?|Test\s+Scenario(?:s)?|Endpoint(?:s)?|HTTP\s+Method|"
            r"Class(?:es)?|Controller(?:s)?|Service(?:s)?|Mapper(?:s)?|"
            r"Repository|Repositories|Method(?:s)?|Function(?:s)?|Release(?:\s+(?:Name|Version))?|"
            r"Test(?:\s+Case)?(?:s)?|Test_Name|Test\s+Name|Commit(?:\s+(?:ID|SHA|Hash))?|"
            r"Git\s+Commit|File(?:s)?|Changed\s+File(?:s)?|Path(?:s)?|Change"
        )
        pattern = (
            rf"(?i)(?:^|[\r\n]|(?<=[.;]))\s*(?:file(?:s)?|changed\s+file(?:s)?|path(?:s)?)"
            rf"\s*[:=-]\s*(.+?)(?=\s*(?:[;\r\n]|$)|\s+(?:{labels})\s*[:=])"
        )
        values: list[str] = []
        for raw in re.findall(pattern, content):
            for part in re.split(r"\s*,\s*", raw.strip()):
                value = part.strip().rstrip(".").strip()
                if value:
                    values.append(value)
        return values

    @staticmethod
    def _without_out_of_scope(content: str) -> str:
        """Remove Out of Scope sections from natural-language entity discovery.

        Explicit labels are still parsed from the full document. This helper is
        used only for inferred requirement prose so excluded concepts do not
        become impacted engineering entities.
        """
        heading = re.compile(
            r"(?im)^\s*(?:\d+[.)]?\s*)?out\s+of\s+scope\s*$"
        )
        match = heading.search(content)
        if not match:
            return content

        start = match.start()
        tail = content[match.end():]
        next_heading = re.search(
            r"(?im)^\s*\d+[.)]?\s+[A-Z][^\r\n]{1,100}\s*$", tail
        )
        end = match.end() + (next_heading.start() if next_heading else len(tail))
        return content[:start] + "\n" + content[end:]

    @classmethod
    def _requirement_prose_entities(cls, content: str) -> tuple[list[str], list[str]]:
        """Extract explicit class/attribute statements from business prose.

        This remains deterministic/offline: it recognizes strong grammatical
        forms instead of guessing arbitrary nouns as code entities.
        """
        active = cls._without_out_of_scope(content)
        attributes: list[str] = []
        classes: list[str] = []

        # Example: "Add a new Student attribute named temporaryLocation."
        for owner, attribute in re.findall(
            r"(?i)\b(?:add|create|introduce|support)\s+(?:a\s+)?(?:new\s+)?"
            r"([A-Z][A-Za-z0-9_$]*)\s+(?:attribute|field|property)\s+"
            r"(?:named|called)\s+([A-Za-z_$][\w$]*)\b",
            active,
        ):
            classes.append(owner)
            attributes.append(attribute)

        # Example: "temporaryLocation belongs to the Student."
        for attribute, owner in re.findall(
            r"\b([a-z_$][A-Za-z0-9_$]*)\s+belongs\s+to\s+(?:the\s+)?"
            r"([A-Z][A-Za-z0-9_$]*)\b",
            active,
        ):
            attributes.append(attribute)
            classes.append(owner)

        # Example: "new attribute named temporaryLocation" when the owning
        # class is not repeated in the same sentence.
        attributes.extend(re.findall(
            r"(?i)\b(?:new\s+)?(?:attribute|field|property)\s+"
            r"(?:named|called)\s+([A-Za-z_$][\w$]*)\b",
            active,
        ))

        return cls._unique(attributes, 100), cls._unique(classes, 100)

    def extract(self, content: str, source_type: str) -> dict[str, Any]:
        source_type = str(source_type or "").strip().upper()
        content = str(content or "")

        jira_ids = self._unique(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", content), 100)
        endpoints = self._unique(
            re.findall(r"(?<!\w)/(?:api/)?[A-Za-z0-9_{}./:-]+", content), 100
        )
        http_methods = self._unique(
            [x.upper() for x in re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b", content, re.I)],
            10,
        )

        release_names = self._unique(
            [x.strip().title() for x in re.findall(self.MONTH_PATTERN, content, re.I)], 20
        )
        release_names += [x for x in self._labeled_values(
            content, r"release(?:\s+(?:name|version))?"
        ) if x.casefold() not in {v.casefold() for v in release_names}]
        release_names = self._unique(release_names, 20)

        # Common identifiers are extracted primarily from explicit labels. This
        # avoids turning every ordinary word in architecture prose into a graph node.
        attributes = self._unique(self._labeled_values(
            content, r"attribute(?:s)?|field(?:s)?|property|properties"
        ))
        scenarios = self._unique(self._labeled_values(
            content, r"scenario(?:s)?|test\s+scenario(?:s)?"
        ))
        classes = self._unique(self._labeled_values(
            content, r"class(?:es)?|controller(?:s)?|service(?:s)?|mapper(?:s)?|repository|repositories"
        ))
        methods = self._unique(self._labeled_values(
            content, r"method(?:s)?|function(?:s)?"
        ))
        tests = self._unique(self._labeled_values(
            content, r"test(?:\s+case)?(?:s)?|test_name|test\s+name"
        ))
        commits = self._unique(self._labeled_values(
            content, r"commit(?:\s+(?:id|sha|hash))?|git\s+commit"
        ))
        files = self._unique(self._file_values(content), 200)

        # Business requirements are often written as prose rather than
        # "Attribute: x" / "Class: Y" labels. Infer only strong, explicit
        # grammatical forms and ignore Out of Scope prose.
        if source_type in {"REQUIREMENT", "JIRA"}:
            prose_attributes, prose_classes = self._requirement_prose_entities(content)
            attributes += prose_attributes
            classes += prose_classes

        # Recognize common code identifiers when they are unambiguous in prose.
        classes += self._unique(re.findall(
            r"\b[A-Z][A-Za-z0-9]*(?:Controller|Service|Mapper|Repository|Client|Gateway)\b", content
        ))
        methods += self._unique(re.findall(
            r"\b(?:[A-Za-z_$][\w$]*\.)?([a-z_$][\w$]*)\s*\(\s*\)", content
        ))
        tests += self._unique(re.findall(r"\b[A-Z][A-Za-z0-9_]*(?:Test|Tests)\b", content))
        commits += self._unique(re.findall(
            r"(?i)\b(?:commit\s+)([0-9a-f]{7,40})\b", content
        ))

        return {
            "jira_ids": self._unique(jira_ids, 100),
            "attributes": self._unique(attributes, 100),
            "scenarios": self._unique(scenarios, 100),
            "endpoints": self._unique(endpoints, 100),
            "http_methods": self._unique(http_methods, 10),
            "classes": self._unique(classes, 100),
            "methods": self._unique(methods, 100),
            "release_names": self._unique(release_names, 20),
            "tests": self._unique(tests, 100),
            "commits": self._unique(commits, 100),
            "files": self._unique(files, 200),
        }
