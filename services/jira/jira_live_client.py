from __future__ import annotations

import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from config import settings


class JiraLiveClient:
    """Small read-only Jira REST client for the first live-Jira milestone."""

    def get_issue(self, issue_key: str) -> dict:
        issue_key = (issue_key or "").strip().upper()
        if not issue_key:
            raise ValueError("Jira issue key is required")
        if not settings.JIRA_LIVE_ENABLED:
            raise ValueError("Live Jira is disabled. Set JIRA_LIVE_ENABLED=true")
        if not settings.JIRA_BASE_URL:
            raise ValueError("JIRA_BASE_URL is required")

        # Jira Cloud REST v3. The fields list keeps the first integration focused.
        fields = "summary,description,status,issuetype,components,labels,priority,assignee,reporter"
        url = (
            f"{settings.JIRA_BASE_URL}/rest/api/3/issue/{quote(issue_key)}"
            f"?fields={fields}"
        )

        headers = {
            "Accept": "application/json",
            "User-Agent": "CodeIntelligence/LiveJira",
        }

        # For Jira Cloud this is normally email + API token.
        # If both are blank, the request is sent without Authorization so this
        # also works with Jira instances that allow anonymous read access.
        if settings.JIRA_EMAIL or settings.JIRA_API_TOKEN:
            if not settings.JIRA_EMAIL or not settings.JIRA_API_TOKEN:
                raise ValueError(
                    "Set both JIRA_EMAIL and JIRA_API_TOKEN, or leave both blank"
                )
            raw = f"{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")

        request = Request(url, headers=headers, method="GET")

        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in (401, 403):
                raise RuntimeError(
                    f"Jira rejected the request ({exc.code}). "
                    "Check Jira access/email/API token. "
                    f"Response: {body[:500]}"
                ) from exc
            if exc.code == 404:
                raise LookupError(
                    f"Jira issue {issue_key} was not found or is not visible"
                ) from exc
            raise RuntimeError(
                f"Jira request failed ({exc.code}): {body[:500]}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Unable to connect to Jira at {settings.JIRA_BASE_URL}: {exc.reason}"
            ) from exc

        return self._normalize_issue(payload)

    @staticmethod
    def _normalize_issue(payload: dict) -> dict:
        fields = payload.get("fields") or {}
        return {
            "source": "LIVE_JIRA",
            "id": payload.get("id"),
            "key": payload.get("key"),
            "summary": fields.get("summary"),
            "description": JiraLiveClient._adf_to_text(fields.get("description")),
            "status": JiraLiveClient._named(fields.get("status")),
            "issue_type": JiraLiveClient._named(fields.get("issuetype")),
            "priority": JiraLiveClient._named(fields.get("priority")),
            "components": [
                item.get("name")
                for item in (fields.get("components") or [])
                if isinstance(item, dict) and item.get("name")
            ],
            "labels": fields.get("labels") or [],
            "assignee": JiraLiveClient._person(fields.get("assignee")),
            "reporter": JiraLiveClient._person(fields.get("reporter")),
        }

    @staticmethod
    def _named(value):
        return value.get("name") if isinstance(value, dict) else None

    @staticmethod
    def _person(value):
        if not isinstance(value, dict):
            return None
        return {
            "display_name": value.get("displayName"),
            "account_id": value.get("accountId"),
        }

    @staticmethod
    def _adf_to_text(value) -> str | None:
        """Convert Jira Cloud Atlassian Document Format to readable plain text."""
        if value is None:
            return None
        if isinstance(value, str):
            return value

        parts: list[str] = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    text = node.get("text")
                    if text:
                        parts.append(text)
                for child in node.get("content") or []:
                    walk(child)
                if node.get("type") in {
                    "paragraph", "heading", "listItem", "bulletList",
                    "orderedList", "blockquote", "codeBlock"
                }:
                    parts.append("\n")
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        text = "".join(parts)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip() or None


jira_live_client = JiraLiveClient()
