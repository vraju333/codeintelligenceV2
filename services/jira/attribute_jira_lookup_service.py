"""Evidence-preserving reverse lookup from an attribute to indexed Jira requirements."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from config import settings
from db_models import JiraKnowledge, KnowledgeSyncSource


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).lower()))


def _mentions(text: str, attribute: str) -> bool:
    """Match camelCase, snake_case, XML PascalCase; avoid generic token overlap."""
    tokens = _tokens(attribute)
    if not tokens:
        return False
    # Attribute spelling may vary by separators and case, but must match a
    # complete identifier, not an unrelated word or substring.
    pattern = r"(?<![A-Za-z0-9])" + r"[_\s.-]*".join(map(re.escape, sorted(tokens, key=lambda x: -len(x)))) + r"(?![A-Za-z0-9])"
    # Preserve attribute token order for countryCode -> country code.
    ordered = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", attribute.replace("_", " "))
    ordered = [x.lower() for x in ordered] or list(tokens)
    pattern = r"(?<![A-Za-z0-9])" + r"[_\s.-]*".join(map(re.escape, ordered)) + r"(?![A-Za-z0-9])"
    return bool(re.search(pattern, text, re.I))


def lookup(db: Session, attribute: str, project_path: str | None = None) -> dict:
    attribute = (attribute or "").strip().split(".")[-1]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{1,127}", attribute):
        return {"status": "INVALID_ATTRIBUTE", "attribute": attribute, "matches": []}

    active = str(project_path or settings.JAVA_PROJECT_PATH or "").strip().replace("\\", "/").rstrip("/").casefold()
    active_name = Path(active.replace("\\", "/")).name
    sources = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.source_type == "JIRA").all()
    # Jira-only scopes are valid, but must be explicitly configured for the
    # active code project or uniquely configured; never claim an implicit link.
    scopes = {active} if active else set()
    linked = [s for s in sources if str(s.project_path or "").strip().replace("\\", "/").rstrip("/").casefold() == active]
    scopes.update(str(s.project_path or "").strip().replace("\\", "/").rstrip("/").casefold() for s in linked)
    if len(sources) == 1 and not linked:
        scopes.add(str(sources[0].project_path or "").strip().casefold())
    rows = db.query(JiraKnowledge).order_by(JiraKnowledge.jira_id).all()
    matches = []
    for row in rows:
        scope = str(row.project_path or "").strip().replace("\\", "/").rstrip("/").casefold()
        if active and scope not in scopes:
            continue
        title = str(row.title or "")
        requirement = str(row.requirement or "")
        if not (_mentions(title, attribute) or _mentions(requirement, attribute)):
            continue
        # Only requirement mentions are proof of requirement-level correlation;
        # neither implies a verified implementation, baseline or Git association.
        excerpt_match = re.search(r".{0,100}" + re.escape(attribute) + r".{0,140}", requirement, re.I | re.S)
        matches.append({
            "jira_id": row.jira_id, "summary": title,
            "relationship": "REQUIREMENT_ATTRIBUTE_MENTION",
            "evidence_source": "INDEXED_JIRA_REQUIREMENT",
            "matched_in": "requirement" if _mentions(requirement, attribute) else "summary",
            "evidence_excerpt": (excerpt_match.group(0).replace("\n", " ") if excerpt_match else title[:180]),
            "jira_scope": row.project_path,
            "implementation_link_verified": False,
            "baseline_link_verified": False,
        })
    return {
        "status": "FOUND" if matches else "NO_INDEXED_MATCH",
        "attribute": attribute, "matches": matches, "match_count": len(matches),
        "coverage": "Indexed JiraKnowledge rows in configured project scope; not an exhaustive live Jira search",
        "project_scope": active_name,
        "note": "Requirement mention is not an explicit Jira-to-code implementation or test-baseline link.",
    }


def indexing_status(db: Session, issue_key: str) -> dict:
    """Inspect the indexed store only; never infer existence from source candidates."""
    issue_key = (issue_key or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", issue_key):
        return {"status": "INVALID_JIRA_KEY", "jira_id": issue_key}
    row = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id == issue_key).first()
    if row is None:
        return {"status": "NOT_INDEXED", "jira_id": issue_key,
                "source": "jira_knowledge", "note": "No stored requirement; live Jira was not queried."}
    return {"status": "INDEXED", "jira_id": row.jira_id,
            "summary": row.title, "description": row.requirement,
            "project_scope": row.project_path, "source": "jira_knowledge",
            "indexed_attributes": sorted(set(re.findall(r"\b[a-z]+[A-Z][A-Za-z0-9]*\b", row.requirement or ""))),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None}
