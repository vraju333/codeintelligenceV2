from __future__ import annotations

import re
from typing import Any


class ValueFlowInvestigationService:
    """Deterministic Phase D reasoning over the trace already produced by
    ScenarioAttributeTraceService.

    This intentionally does NOT rescan the whole Java project. The existing
    trace service remains the source of code evidence; this service classifies
    and ranks that evidence to identify likely divergence points.
    """

    RISK_WEIGHT = {
        "NULL_OVERWRITE": 120,
        "MISSING_MAPPING": 115,
        "CONDITIONAL_PATH": 100,
        "VALUE_TRANSFORMED": 110,
        "VALUE_OVERWRITTEN": 125,
        "OVERWRITE": 85,
        "MAPPING": 70,
        "ASSIGNMENT": 65,
        "PERSISTENCE": 45,
        "READ": 20,
        "REFERENCE": 10,
    }

    def analyze(
        self,
        *,
        attribute_name: str,
        trace: dict,
        differences: list[dict],
        input_context: Any = None,
    ) -> dict:

        evidence = self._flatten_trace_evidence(trace)
        classified = [
            self._classify_evidence(attribute_name, item)
            for item in evidence
        ]

        ranked = sorted(
            classified,
            key=lambda item: (
                -item["score"],
                item.get("line_number") or 10**9,
            )
        )

        difference = differences[0] if differences else {}
        input_value, input_path = self._find_input_value(
            input_context,
            attribute_name,
        )

        likely = self._choose_likely_divergence(
            attribute_name=attribute_name,
            difference=difference,
            input_value=input_value,
            ranked=ranked,
        )

        return {
            "attribute": attribute_name,
            "expected": difference.get("expected"),
            "actual": difference.get("actual"),
            "input_value": input_value,
            "input_path": input_path,
            "evidence_count": len(classified),
            "value_flow_evidence": classified,
            "ranked_code_locations": ranked[:12],
            "likely_divergence": likely.get("likely_divergence"),
            "conclusion": likely,
        }

    def _flatten_trace_evidence(self, trace: dict) -> list[dict]:
        result = []
        seen = set()

        for step in trace.get("trace", []) or []:
            class_name = step.get("class_name")
            method_name = step.get("method_name")

            for evidence in step.get("evidence", []) or []:
                item = dict(evidence)
                item.setdefault("class_name", class_name)
                item.setdefault("method_name", method_name)

                key = (
                    item.get("class_name"),
                    item.get("method_name"),
                    item.get("line_number"),
                    item.get("code"),
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)

        # Some trace implementations expose raw occurrences separately.
        for evidence in trace.get("occurrences", []) or []:
            item = dict(evidence)
            key = (
                item.get("class_name"),
                item.get("method_name"),
                item.get("line_number"),
                item.get("code"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

        return result

    def _classify_evidence(
        self,
        attribute_name: str,
        evidence: dict,
    ) -> dict:

        item = dict(evidence)
        code = str(item.get("code") or "").strip()
        existing_usage = str(item.get("usage_type") or "").upper()

        # Java comments are non-executable evidence. Do not classify a
        # commented-out setter/condition/transformation as live code.
        if self._is_commented_out(code):
            item.update({
                "usage_type": "COMMENTED_OUT",
                "risk_signal": None,
                "reason": "The attribute appears only in commented-out code.",
                "score": 0,
            })
            return item

        cap = (
            attribute_name[:1].upper()
            + attribute_name[1:]
            if attribute_name
            else ""
        )

        usage = existing_usage or "REFERENCE"
        risk_signal = None
        reason = "Attribute reference found in the discovered code flow."

        setter = re.search(
            rf"\.set{re.escape(cap)}\s*\((.*?)\)",
            code,
            re.I,
        )

        direct_assignment = re.search(
            rf"\b{re.escape(attribute_name)}\s*=\s*(?!=)(.+?);?$",
            code,
            re.I,
        )

        accessor = re.search(
            rf"\.(?:get|is){re.escape(cap)}\s*\(\s*\)"
            rf"|\.{re.escape(attribute_name)}\s*\(\s*\)",
            code,
            re.I,
        )

        conditional = self._attribute_controls_condition(
            code=code,
            attribute_name=attribute_name,
            cap=cap,
        )

        if setter:
            rhs = setter.group(1).strip()

            if re.search(r"\bnull\b", rhs, re.I):
                usage = "NULL_OVERWRITE"
                risk_signal = "NULL_OVERWRITE"
                reason = (
                    "The attribute is explicitly assigned null at this point."
                )
            elif (
                existing_usage == "VALUE_TRANSFORMED"
                or self._looks_transformed(code, attribute_name, cap)
                or self._looks_transformed(rhs, attribute_name, cap)
            ):
                # Preserve transformation evidence already discovered by the
                # lineage layer. Also inspect the complete Java statement:
                # the setter regex can only capture up to the first nested ')'
                # in expressions such as r.primaryEmail().toUpperCase().
                usage = "VALUE_TRANSFORMED"
                risk_signal = "VALUE_TRANSFORMED"
                reason = (
                    "The attribute is transformed before it is assigned."
                )
            else:
                usage = "MAPPING"
                reason = (
                    "The attribute is mapped from one object to another here."
                )

        elif direct_assignment:
            rhs = direct_assignment.group(1).strip()

            if re.search(r"\bnull\b", rhs, re.I):
                usage = "NULL_OVERWRITE"
                risk_signal = "NULL_OVERWRITE"
                reason = (
                    "The attribute is explicitly overwritten with null."
                )
            elif self._looks_transformed(rhs, attribute_name, cap):
                usage = "VALUE_TRANSFORMED"
                risk_signal = "VALUE_TRANSFORMED"
                reason = (
                    "The assigned value is transformed before storage."
                )
            else:
                usage = "ASSIGNMENT"
                reason = "The attribute is assigned at this point."

        elif conditional:
            usage = "CONDITIONAL_PATH"
            risk_signal = "CONDITIONAL_PATH"
            reason = (
                "A condition can allow or skip propagation of the attribute."
            )

        elif ".save(" in code or "repository.save" in code.lower():
            usage = "PERSISTENCE"
            reason = (
                "The object carrying the attribute reaches persistence here."
            )

        elif accessor:
            usage = "READ"
            reason = "The attribute value is read here."

        score = self.RISK_WEIGHT.get(
            usage,
            self.RISK_WEIGHT.get(existing_usage, 10),
        )

        if item.get("direct_attribute_touch"):
            score += 20

        item.update({
            "usage_type": usage,
            "risk_signal": risk_signal,
            "reason": reason,
            "score": score,
        })
        return item

    @staticmethod
    def _is_commented_out(code: str) -> bool:
        """Return True when an evidence snippet is Java comment-only code."""
        value = str(code or "").strip()
        if not value:
            return False

        # Single-line comment.
        if value.startswith("//"):
            return True

        # A complete block-comment snippet.
        if value.startswith("/*") and value.endswith("*/"):
            return True

        # Common per-line block-comment forms produced by scanners.
        if value.startswith("*") or value.startswith("*/"):
            return True

        return False

    @staticmethod
    def _attribute_controls_condition(
        *,
        code: str,
        attribute_name: str,
        cap: str,
    ) -> bool:
        """True only when the investigated attribute participates in the condition.

        This deliberately excludes generic object null guards such as:
        e == null ? null : new Response(e.getPrimaryEmail())
        because primaryEmail is only in the result expression, not the condition.
        """
        if not code:
            return False

        condition_texts = []

        for match in re.finditer(
                r"\bif\s*\((.*?)\)",
                code,
                re.I
        ):
            condition_texts.append(match.group(1))

        if "?" in code:
            condition_texts.append(code.split("?", 1)[0])

        if "&&" in code or "||" in code:
            condition_texts.append(code)

        if not condition_texts:
            return False

        attribute_pattern = re.compile(
            rf"\b{re.escape(attribute_name)}\b"
            rf"|\.(?:get|is){re.escape(cap)}\s*\(\s*\)"
            rf"|\.{re.escape(attribute_name)}\s*\(\s*\)",
            re.I,
        )

        return any(attribute_pattern.search(part) for part in condition_texts)

    @staticmethod
    def _looks_transformed(
        rhs: str,
        attribute_name: str,
        cap: str,
    ) -> bool:

        # Direct pass-throughs are not transformations:
        # request.primaryEmail()
        # request.getPrimaryEmail()
        direct_patterns = [
            rf"[A-Za-z_]\w*\.{re.escape(attribute_name)}\s*\(\s*\)",
            rf"[A-Za-z_]\w*\.(?:get|is){re.escape(cap)}\s*\(\s*\)",
            rf"[A-Za-z_]\w*",
        ]

        if any(
            re.fullmatch(pattern, rhs, re.I)
            for pattern in direct_patterns
        ):
            return False

        return bool(
            re.search(
                r"\.(?:trim|strip|toLowerCase|toUpperCase|replace|substring)\s*\("
                r"|Optional\.|Objects\.|String\.format|valueOf\s*\(",
                rhs,
                re.I,
            )
            or "+" in rhs
        )

    def _choose_likely_divergence(
        self,
        *,
        attribute_name: str,
        difference: dict,
        input_value: Any,
        ranked: list[dict],
    ) -> dict:

        expected = difference.get("expected")
        actual = difference.get("actual")

        if input_value is None and expected is not None:
            return {
                "classification": "INPUT_MISSING_OR_NULL",
                "confidence": "HIGH",
                "summary": (
                    f"{attribute_name} is expected in the result, but no "
                    "non-null input value was found for the attribute."
                ),
                "likely_divergence": None,
            }

        risky = [
            item
            for item in ranked
            if item.get("risk_signal")
            in {
                "NULL_OVERWRITE",
                "CONDITIONAL_PATH",
                "VALUE_TRANSFORMED",
            }
        ]

        if risky:
            top = risky[0]
            return {
                "classification": top["risk_signal"],
                "confidence": "HIGH",
                "summary": top["reason"],
                "likely_divergence": self._location(top),
                "line_number": top.get("line_number"),
                "code": top.get("code"),
            }

        mappings = [
            item
            for item in ranked
            if item.get("usage_type") in {"MAPPING", "ASSIGNMENT"}
            and not self._is_plain_model_accessor(item, attribute_name)
        ]

        commented_mapping = next(
            (
                item
                for item in ranked
                if item.get("usage_type") == "COMMENTED_OUT"
                and self._looks_like_attribute_write(
                    str(item.get("code") or ""),
                    attribute_name,
                )
            ),
            None,
        )

        if actual is None and expected is not None and not mappings:
            if commented_mapping:
                return {
                    "classification": "MISSING_MAPPING",
                    "confidence": "HIGH",
                    "summary": (
                        f"No executable mapping for {attribute_name} was found. "
                        "A likely mapping statement is commented out."
                    ),
                    "likely_divergence": self._location(commented_mapping),
                    "line_number": commented_mapping.get("line_number"),
                    "code": commented_mapping.get("code"),
                }

            return {
                "classification": "MISSING_MAPPING",
                "confidence": "MEDIUM",
                "summary": (
                    f"No assignment/mapping for {attribute_name} was found "
                    "in the discovered attribute trace."
                ),
                "likely_divergence": None,
            }

        overwrite = self._find_proven_overwrite(
            attribute_name=attribute_name,
            actual=actual,
            mappings=mappings,
        )

        if overwrite:
            return {
                "classification": "VALUE_OVERWRITTEN",
                "confidence": "HIGH",
                "summary": overwrite["summary"],
                "likely_divergence": self._location(overwrite["item"]),
                "line_number": overwrite["item"].get("line_number"),
                "code": overwrite["item"].get("code"),
            }

        if mappings:
            top = mappings[0]
            return {
                "classification": "MAPPING_REVIEW_REQUIRED",
                "confidence": "MEDIUM",
                "summary": (
                    f"The strongest value-modification point for "
                    f"{attribute_name} is {self._location(top)}."
                ),
                "likely_divergence": self._location(top),
                "line_number": top.get("line_number"),
                "code": top.get("code"),
            }

        return {
            "classification": "NO_DETERMINISTIC_DIVERGENCE_FOUND",
            "confidence": "LOW",
            "summary": (
                "The static trace contains the attribute, but the available "
                "evidence does not prove the exact runtime divergence."
            ),
            "likely_divergence": None,
        }

    def _find_proven_overwrite(
        self,
        *,
        attribute_name: str,
        actual: Any,
        mappings: list[dict],
    ) -> dict | None:
        """Detect a later same-method literal write matching the observed actual value."""
        if len(mappings) < 2:
            return None

        ordered = sorted(
            mappings,
            key=lambda item: (
                str(item.get("class_name") or ""),
                str(item.get("method_name") or ""),
                item.get("line_number") or 10**9,
            ),
        )

        for index, later in enumerate(ordered):
            later_line = later.get("line_number")
            if later_line is None:
                continue

            later_value = self._extract_written_literal(
                str(later.get("code") or ""),
                attribute_name,
            )
            if later_value is self._NO_LITERAL:
                continue

            earlier = next(
                (
                    item for item in reversed(ordered[:index])
                    if item.get("class_name") == later.get("class_name")
                    and item.get("method_name") == later.get("method_name")
                    and (item.get("line_number") or -1) < later_line
                    and self._looks_like_attribute_write(
                        str(item.get("code") or ""), attribute_name
                    )
                ),
                None,
            )
            if earlier is None:
                continue

            if self._values_equal(later_value, actual):
                result_item = dict(later)
                result_item.update({
                    "usage_type": "VALUE_OVERWRITTEN",
                    "risk_signal": "VALUE_OVERWRITTEN",
                    "score": self.RISK_WEIGHT["VALUE_OVERWRITTEN"],
                    "reason": (
                        f"{attribute_name} is written earlier in "
                        f"{self._location(earlier)} and then overwritten here "
                        f"with the observed actual value."
                    ),
                })
                return {
                    "item": result_item,
                    "summary": (
                        f"{attribute_name} is first mapped on line "
                        f"{earlier.get('line_number')} and then overwritten on "
                        f"line {later_line}. The later value matches the observed "
                        f"actual value, so this is the likely root cause."
                    ),
                }

        return None

    _NO_LITERAL = object()

    @classmethod
    def _extract_written_literal(cls, code: str, attribute_name: str) -> Any:
        cap = (
            attribute_name[:1].upper() + attribute_name[1:]
            if attribute_name else ""
        )

        setter = re.search(
            rf"\.set{re.escape(cap)}\s*\(\s*"
            r"(null|true|false|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|-?\d+(?:\.\d+)?)\s*\)",
            code,
            re.I,
        )
        if setter:
            return cls._parse_java_literal(setter.group(1))

        assignment = re.search(
            rf"\b{re.escape(attribute_name)}\s*=\s*"
            r"(null|true|false|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|-?\d+(?:\.\d+)?)\s*;",
            code,
            re.I,
        )
        if assignment:
            return cls._parse_java_literal(assignment.group(1))

        return cls._NO_LITERAL

    @staticmethod
    def _parse_java_literal(value: str) -> Any:
        raw = str(value).strip()
        lower = raw.lower()

        if lower == "null":
            return None
        if lower == "true":
            return True
        if lower == "false":
            return False

        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            body = raw[1:-1]
            body = body.replace(r"\\", "\\")
            body = body.replace(r"\"", '"').replace(r"\'", "'")
            body = body.replace(r"\n", "\n").replace(r"\t", "\t")
            return body

        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        if left is None or right is None:
            return left is None and right is None
        if isinstance(left, bool) or isinstance(right, bool):
            return left == right
        return str(left) == str(right)

    @staticmethod
    def _looks_like_attribute_write(
        code: str,
        attribute_name: str,
    ) -> bool:
        cap = (
            attribute_name[:1].upper() + attribute_name[1:]
            if attribute_name
            else ""
        )
        return bool(
            re.search(
                rf"\.set{re.escape(cap)}\s*\(",
                str(code or ""),
                re.I,
            )
            or re.search(
                rf"\b{re.escape(attribute_name)}\s*=",
                str(code or ""),
                re.I,
            )
        )

    @staticmethod
    def _is_plain_model_accessor(
        item: dict,
        attribute_name: str,
    ) -> bool:
        """Exclude entity/DTO getter-setter declarations from mapping evidence."""
        method_name = str(item.get("method_name") or "")
        code = str(item.get("code") or "").strip()
        cap = (
            attribute_name[:1].upper() + attribute_name[1:]
            if attribute_name
            else ""
        )

        if method_name.lower() in {
            f"get{cap}".lower(),
            f"set{cap}".lower(),
            f"is{cap}".lower(),
        }:
            return True

        # Compact one-line Java entity declarations can contain both getter
        # and setter methods. They prove field access exists, not that the
        # request value is mapped through the business flow.
        getter_decl = re.search(
            rf"\b(?:get|is){re.escape(cap)}\s*\(",
            code,
            re.I,
        )
        setter_decl = re.search(
            rf"\bset{re.escape(cap)}\s*\(",
            code,
            re.I,
        )
        return bool(
            ("public " in code or "private " in code or "protected " in code)
            and (getter_decl or setter_decl)
        )

    @staticmethod
    def _location(item: dict) -> str | None:
        class_name = item.get("class_name")
        method_name = item.get("method_name")

        if class_name and method_name:
            return f"{class_name}.{method_name}"

        return class_name or method_name

    def _find_input_value(
        self,
        input_context: Any,
        attribute_name: str,
    ) -> tuple[Any, str | None]:

        target = self._normalize(attribute_name)

        def walk(value: Any, path: str):
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = (
                        f"{path}.{key}"
                        if path
                        else str(key)
                    )

                    if self._normalize(key) == target:
                        return child, child_path

                    found_value, found_path = walk(
                        child,
                        child_path,
                    )

                    if found_path is not None:
                        return found_value, found_path

            elif isinstance(value, list):
                for index, child in enumerate(value):
                    found_value, found_path = walk(
                        child,
                        f"{path}[{index}]",
                    )

                    if found_path is not None:
                        return found_value, found_path

            return None, None

        return walk(input_context, "")

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(
            r"[^a-z0-9]",
            "",
            str(value or "").lower(),
        )
