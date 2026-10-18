"""Test suite retrieval and historical JIRA attachment import (explicit, project-scoped)."""
from __future__ import annotations

import base64
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from services.knowledge.test_suite_service import TestSuiteService, SUPPORTED, MAX_BYTES
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

router = APIRouter(prefix='/api/test-suites', tags=['Test Suites'])
registry = TestSuiteService()


class _NoRedirect(HTTPRedirectHandler):
    """Avoid silently sending Jira Authorization to an attachment CDN."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _jira_attachment_bytes(sync, attachment_id: str, content_url: str) -> bytes:
    """Download through the authenticated Jira API without credential-leaking redirects."""
    base = settings.JIRA_BASE_URL.rstrip('/')
    base_host = urlparse(base).netloc.lower()
    auth = None
    if settings.JIRA_EMAIL and settings.JIRA_API_TOKEN:
        encoded = base64.b64encode(
            f'{settings.JIRA_EMAIL}:{settings.JIRA_API_TOKEN}'.encode()
        ).decode()
        auth = 'Basic ' + encoded

    opener = build_opener(_NoRedirect)
    # Atlassian Cloud supports redirect=false on the attachment-content API.
    # It is preferable to downloading the metadata content URL, which may
    # redirect to a separate CDN host.
    api_url = f'{base}/rest/api/3/attachment/content/{quote(attachment_id, safe="")}?redirect=false'
    candidates = [api_url]
    if content_url and content_url != api_url:
        candidates.append(content_url)
    failures = []

    for candidate in candidates:
        parsed = urlparse(candidate)
        if parsed.scheme != 'https' or parsed.netloc.lower() != base_host:
            failures.append('Rejected attachment URL outside configured Jira host')
            continue
        headers = {'Accept': '*/*', 'User-Agent': 'CodeIntelligence/TestSuite'}
        if auth:
            headers['Authorization'] = auth
        request = Request(candidate, headers=headers)
        try:
            with opener.open(request, timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
                content_type = response.headers.get('Content-Type', '')
            if len(data) > MAX_BYTES:
                raise ValueError(f'Attachment exceeds {MAX_BYTES} bytes')
            # HTML / JSON usually indicates an auth page or API error, not an Excel file.
            if 'text/html' in content_type.lower() or 'application/json' in content_type.lower():
                raise ValueError(f'Unexpected attachment response Content-Type: {content_type}')
            return data
        except HTTPError as exc:
            # A redirect may point to an Atlassian CDN. Do not forward Jira auth
            # to that destination; report it rather than following blindly.
            if exc.code in (301, 302, 303, 307, 308):
                target = exc.headers.get('Location', '')
                failures.append(f'HTTP {exc.code} redirect to {urlparse(target).netloc or "unknown host"} (not followed)')
            else:
                failures.append(f'HTTP {exc.code} {exc.reason}')
        except (URLError, ValueError) as exc:
            failures.append(str(exc))

    raise ValueError('Jira attachment download failed: ' + '; '.join(failures))


@router.get('/search')
def search(project_path: str, jira: str = '', attribute: str = '', db: Session = Depends(get_db)):
    if not project_path.strip():
        raise HTTPException(400, 'project_path is required')
    return registry.search(db, project_path=project_path, jira=jira, attribute=attribute)


@router.post('/jira/{jira_key}/sync')
def sync_jira_test_suites(
    jira_key: str,
    project_path: str,
    attachment_ids: list[str] | None = Query(None),
    db: Session = Depends(get_db),
):
    """Import real test-suite attachments, preserving Jira and source provenance."""
    jira_key = jira_key.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*-\d+', jira_key):
        raise HTTPException(400, 'Invalid Jira key')
    if not project_path.strip():
        raise HTTPException(400, 'project_path is required')
    if not settings.JIRA_LIVE_ENABLED:
        raise HTTPException(400, 'Live Jira is disabled')

    sync = EnterpriseKnowledgeSyncService()
    try:
        issue = sync._jira_request(f'/rest/api/3/issue/{jira_key}?fields=attachment')
    except Exception as exc:
        raise HTTPException(502, f'Unable to read Jira attachments: {exc}') from exc

    attachments = (issue.get('fields') or {}).get('attachment') or []
    imported, errors = [], []
    for item in attachments:
        attachment_id = str(item.get('id') or '')
        if attachment_ids is not None and attachment_id not in attachment_ids:
            continue
        filename = str(item.get('filename') or '')
        if not any(filename.lower().endswith(ext) for ext in SUPPORTED):
            continue
        if not attachment_id.isdigit():
            errors.append({'filename': filename, 'stage': 'metadata', 'error': 'Invalid attachment ID'})
            continue
        if int(item.get('size') or 0) > MAX_BYTES:
            errors.append({'filename': filename, 'stage': 'metadata', 'error': f'Attachment exceeds {MAX_BYTES} bytes'})
            continue
        try:
            content = _jira_attachment_bytes(sync, attachment_id, str(item.get('content') or ''))
        except Exception as exc:
            errors.append({'filename': filename, 'stage': 'download', 'error': str(exc)})
            continue
        try:
            result = registry.ingest(
                db, filename=filename, data=content, project_path=project_path,
                jira_key=jira_key, source_ref=f'jira:{jira_key}:attachment:{attachment_id}',
                source_kind='JIRA',
            )
            imported.append(result)
        except Exception as exc:
            db.rollback()
            errors.append({'filename': filename, 'stage': 'ingest', 'error': str(exc)})

    return {
        'jira': jira_key, 'project_path': project_path, 'attachments': len(attachments),
        'imported': imported, 'errors': errors,
        'status': 'PARTIAL' if errors else 'SUCCESS',
    }
