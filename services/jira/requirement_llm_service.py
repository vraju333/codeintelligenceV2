from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from config import settings


class RequirementLlmService:
    """Convert Jira prose into structured requirement intent.

    Only the Jira/requirement text is sent to the configured LLM provider.
    Java source code, RAG documents, endpoint flows and scenarios are never sent.

    Providers:
      - ollama: local/private inference (default)
      - openai: optional hosted provider
      - azure_openai: Azure OpenAI / Azure AI Foundry deployment
    """

    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "ADD", "UPDATE", "REMOVE", "READ", "PERSIST",
                    "VALIDATE", "BUSINESS_RULE", "CHANGE"
                ],
            },
            "entity": {"type": ["string", "null"]},
            "attribute": {"type": ["string", "null"]},
            "parent": {"type": ["string", "null"]},
            "attributes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "condition": {"type": ["string", "null"]},
            "desired_behavior": {"type": ["string", "null"]},
            "concepts": {
                "type": "array",
                "items": {"type": "string"},
            },
            "intents": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "ADD", "UPDATE", "REMOVE", "READ", "PERSIST",
                        "VALIDATE", "BUSINESS_RULE", "CHANGE"
                    ],
                },
            },
        },
        "required": [
            "action", "entity", "attribute", "parent", "attributes",
            "condition", "desired_behavior", "concepts", "intents"
        ],
        "additionalProperties": False,
    }

    def provider(self) -> str:
        return (settings.LLM_PROVIDER or "ollama").strip().lower()

    def is_configured(self) -> bool:
        provider = self.provider()
        if provider == "ollama":
            return bool((settings.OLLAMA_BASE_URL or "").strip() and (settings.OLLAMA_MODEL or "").strip())
        if provider == "openai":
            return bool((settings.OPENAI_API_KEY or "").strip())
        if provider in {"azure", "azure_openai"}:
            return bool(
                self._setting("AZURE_OPENAI_ENDPOINT")
                and self._setting("AZURE_OPENAI_DEPLOYMENT")
                and (
                    self._setting("AZURE_OPENAI_API_KEY")
                    or self._setting("AZURE_OPENAI_TOKEN")
                )
            )
        return False

    def parse(self, requirement: str) -> dict:
        provider = self.provider()
        if provider == "ollama":
            parsed = self._parse_with_ollama(requirement)
        elif provider == "openai":
            parsed = self._parse_with_openai(requirement)
        elif provider in {"azure", "azure_openai"}:
            parsed = self._parse_with_azure_openai(requirement)
        else:
            raise RuntimeError(
                f"Unsupported LLM_PROVIDER '{provider}'. Use 'ollama', 'openai' or 'azure_openai'."
            )
        return self._normalize(parsed)

    def _setting(self, name: str, default: str = "") -> str:
        return str(getattr(settings, name, None) or os.getenv(name) or default or "").strip()

    def _prompt(self, requirement: str) -> str:
        return f"""Convert the Jira requirement below into compact structured JSON.
Do not infer Java classes, endpoints, DTO names, services, mappers, repositories or implementation details.
Only understand the business requirement itself.

Rules:
- entity = the primary business/domain object being changed, e.g. Student or Employee.
- attribute = the main field being added/changed when one is clearly present.
- parent = the containing business concept when nested, e.g. 'email address'.
- action = BUSINESS_RULE for conditional business behavior such as 'if GPA > 7 then eligible'.
- condition = preserve the business condition in compact plain language when present.
- desired_behavior = the expected outcome.
- concepts = important business nouns/phrases only.
- intents may contain multiple relevant actions.
- Never invent source-code concepts.
- Return JSON only.

Jira requirement:
{requirement}
"""

    def _parse_with_ollama(self, requirement: str) -> dict:
        payload = {
            "model": settings.OLLAMA_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": self._prompt(requirement),
                }
            ],
            "stream": False,
            "format": self.RESPONSE_SCHEMA,
            "options": {
                "temperature": 0,
            },
        }

        url = f"{settings.OLLAMA_BASE_URL}/api/chat"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Ollama requirement parsing failed ({exc.code}): {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "Cannot connect to local Ollama at "
                f"{settings.OLLAMA_BASE_URL}. Start Ollama and ensure model "
                f"'{settings.OLLAMA_MODEL}' is installed. Details: {exc.reason}"
            ) from exc

        content = ((body.get("message") or {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("Ollama returned no structured requirement output")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Ollama returned invalid JSON: {content[:500]}"
            ) from exc

    def _parse_with_openai(self, requirement: str) -> dict:
        if not (settings.OPENAI_API_KEY or "").strip():
            raise RuntimeError("OPENAI_API_KEY is not configured")

        payload = {
            "model": settings.OPENAI_MODEL,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": self._prompt(requirement)}
                    ],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "jira_requirement_understanding",
                    "strict": True,
                    "schema": self.RESPONSE_SCHEMA,
                }
            },
        }

        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"OpenAI requirement parsing failed ({exc.code}): {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI requirement parsing failed: {exc.reason}") from exc

        output_text = self._extract_openai_output_text(body)
        if not output_text:
            raise RuntimeError("OpenAI returned no structured requirement output")
        return json.loads(output_text)

    def _parse_with_azure_openai(self, requirement: str) -> dict:
        endpoint = self._setting("AZURE_OPENAI_ENDPOINT").rstrip("/")
        deployment = self._setting("AZURE_OPENAI_DEPLOYMENT")
        api_version = self._setting("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        api_key = self._setting("AZURE_OPENAI_API_KEY")
        token = self._setting("AZURE_OPENAI_TOKEN")

        if not endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not configured")
        if not deployment:
            raise RuntimeError("AZURE_OPENAI_DEPLOYMENT is not configured")
        if not api_key and not token:
            raise RuntimeError("Configure AZURE_OPENAI_API_KEY or AZURE_OPENAI_TOKEN")


        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": self._prompt(requirement),
                }
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "jira_requirement_understanding",
                    "strict": True,
                    "schema": self.RESPONSE_SCHEMA,
                },
            },
        }

        url = (
            f"{endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={api_version}"
        )
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {token}"

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Azure OpenAI requirement parsing failed ({exc.code}): {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Azure OpenAI requirement parsing failed: {exc.reason}"
            ) from exc

        output_text = self._extract_chat_output_text(body)
        if not output_text:
            raise RuntimeError("Azure OpenAI returned no structured requirement output")
        return json.loads(output_text)

    def _extract_openai_output_text(self, response: dict) -> str | None:
        if isinstance(response.get("output_text"), str):
            return response["output_text"]

        for item in response.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    return content["text"]
        return None

    def _extract_chat_output_text(self, response: dict) -> str | None:
        choices = response.get("choices") or []
        if not choices:
            return None

        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip() or None

        return None

    def _normalize(self, data: dict) -> dict:
        def clean(value):
            if isinstance(value, str):
                value = " ".join(value.strip().split())
                return value or None
            return value

        attributes = []
        for value in data.get("attributes", []) or []:
            value = clean(value)
            if value and value.lower() not in {x.lower() for x in attributes}:
                attributes.append(value)

        main_attribute = clean(data.get("attribute"))
        if main_attribute and main_attribute.lower() not in {x.lower() for x in attributes}:
            attributes.insert(0, main_attribute)

        concepts = []
        for value in data.get("concepts", []) or []:
            value = clean(value)
            if value and value.lower() not in {x.lower() for x in concepts}:
                concepts.append(value)

        entity = clean(data.get("entity"))
        parent = clean(data.get("parent"))
        for value in [main_attribute, parent, entity]:
            if value and value.lower() not in {x.lower() for x in concepts}:
                concepts.append(value)

        intents = []
        for value in data.get("intents", []) or []:
            value = str(value).upper().strip()
            if value and value not in intents:
                intents.append(value)
        action = str(data.get("action") or "CHANGE").upper().strip()
        if action not in intents:
            intents.insert(0, action)

        return {
            "action": action,
            "entity": entity,
            "attribute": main_attribute,
            "parent": parent,
            "attributes": attributes,
            "condition": clean(data.get("condition")),
            "desired_behavior": clean(data.get("desired_behavior")),
            "concepts": concepts[:15],
            "intents": intents,
            "llm_provider": self.provider(),
        }
