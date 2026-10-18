"""Cycle 1A / 1B: JIRA SDLC reports, test evidence and immutable release snapshots."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from sdlc_models import SdlcTestExecution, SdlcRelease, SdlcReleaseSnapshot
from services.agent.sdlc_report_service import build_report, jira_key, project_path, release_report, scope
from services.knowledge.test_suite_service import TestSuiteService

router = APIRouter(prefix='/api/sdlc', tags=['SDLC Reports and Baselines'])


class ExecutionRequest(BaseModel):
    project_path: str
    jira_key: str
    test_id: str
    status: str
    git_revision: str
    evidence_ref: str
    executed_at: datetime
    recorded_by: str
    notes: str | None = None


class ReleaseRequest(BaseModel):
    project_path: str
    release_key: str
    jira_keys: list[str] = Field(min_length=1)


class CaptureRequest(BaseModel):
    project_path: str
    captured_by: str


def _release(db, project, key):
    rows = db.query(SdlcRelease).filter(SdlcRelease.release_key == key).all()
    release = next((r for r in rows if scope(r.project_path) == scope(project)), None)
    if not release:
        raise HTTPException(404, 'Release not found for this project')
    return release


@router.get('/jira/{jira}/report')
def jira_report(jira: str, project_path: str = Query(...), db: Session = Depends(get_db)):
    try:
        return build_report(db, jira=jira, project=project_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/executions', status_code=201)
def record_execution(payload: ExecutionRequest, db: Session = Depends(get_db)):
    try:
        key = jira_key(payload.jira_key)
        project = project_path(payload.project_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    status = payload.status.strip().upper()
    if status not in {'PASSED', 'FAILED', 'BLOCKED', 'SKIPPED'}:
        raise HTTPException(400, 'status must be PASSED, FAILED, BLOCKED or SKIPPED')
    if not re.fullmatch(r'[0-9a-fA-F]{7,40}', payload.git_revision.strip()):
        raise HTTPException(400, 'git_revision must be a Git SHA (7-40 hexadecimal characters)')
    if not all([payload.test_id.strip(), payload.evidence_ref.strip(), payload.recorded_by.strip()]):
        raise HTTPException(400, 'test_id, evidence_ref and recorded_by are required')
    if payload.executed_at.tzinfo is None:
        raise HTTPException(400, 'executed_at must contain a timezone')
    records = TestSuiteService().search(db, project_path=project, jira=key, limit=500)['test_cases']
    if payload.test_id.casefold() not in {str(c.get('test_id') or '').casefold() for c in records}:
        raise HTTPException(400, 'Test ID not found in imported QA suites for this JIRA and project')
    # Preserve every execution event. Reports display the latest recorded event per test ID.
    row = SdlcTestExecution(project_path=project, jira_key=key, test_id=payload.test_id.strip(),
                            status=status, git_revision=payload.git_revision.strip().lower(),
                            evidence_ref=payload.evidence_ref.strip(), executed_at=payload.executed_at,
                            recorded_by=payload.recorded_by.strip(), notes=payload.notes)
    db.add(row)
    db.commit()
    db.refresh(row)
    return {'id': row.id, 'status': status, 'jira': key, 'test_id': row.test_id,
            'evidence_type': 'USER_RECORDED_NOT_INDEPENDENTLY_VERIFIED'}


@router.post('/releases')
def upsert_release(payload: ReleaseRequest, db: Session = Depends(get_db)):
    try:
        project = project_path(payload.project_path)
        keys = sorted(set(jira_key(k) for k in payload.jira_keys))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    key = payload.release_key.strip()
    if not key or len(key) > 150:
        raise HTTPException(400, 'release_key must be 1-150 characters')
    rows = db.query(SdlcRelease).filter(SdlcRelease.release_key == key).all()
    release = next((r for r in rows if scope(r.project_path) == scope(project)), None)
    if release:
        release.jira_keys_json = json.dumps(keys)
    else:
        release = SdlcRelease(project_path=project, release_key=key, jira_keys_json=json.dumps(keys))
        db.add(release)
    db.commit()
    return {'release_key': key, 'project_path': project, 'jira_keys': keys,
            'note': 'Release membership is explicit; no automatic Git-based inclusion'}


@router.get('/releases/{release_key}/report')
def get_release_report(release_key: str, project_path: str = Query(...), db: Session = Depends(get_db)):
    release = _release(db, project_path, release_key)
    try:
        return release_report(db, release)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/releases/{release_key}/capture')
def capture_baseline(release_key: str, payload: CaptureRequest, db: Session = Depends(get_db)):
    if not payload.captured_by.strip():
        raise HTTPException(400, 'captured_by is required')
    release = _release(db, payload.project_path, release_key)
    try:
        report = release_report(db, release)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # Baseline version increments only when explicit scope or QA testing evidence changes.
    # Git/source changes alone never create a new testing baseline version.
    testing = [{'jira': r['jira'], 'tests': [
        {'test_id': c['test_id'], 'scenario': c['scenario'], 'expected': c['expected'],
         'declared_operation': c['declared_operation'], 'sources': c['sources'],
         'execution_status': c['execution_status'], 'execution_evidence': c.get('execution_evidence')}
        for c in r['qa']['test_cases']]} for r in report['jira_reports']]
    fingerprint = hashlib.sha256(json.dumps({'jira_keys': report['jira_keys'], 'testing': testing},
                                           sort_keys=True, default=str).encode()).hexdigest()
    previous = db.query(SdlcReleaseSnapshot).filter(SdlcReleaseSnapshot.release_id == release.id).order_by(SdlcReleaseSnapshot.version.desc()).first()
    if previous and previous.fingerprint == fingerprint:
        return {'status': 'UNCHANGED', 'version': previous.version, 'snapshot_id': previous.id,
                'note': 'No new baseline version because testing evidence and JIRA membership are unchanged'}
    version = previous.version + 1 if previous else 1
    report['baseline'] = {'version': version, 'captured_by': payload.captured_by.strip(),
                          'captured_at': datetime.now(timezone.utc).isoformat()}
    snapshot = SdlcReleaseSnapshot(release_id=release.id, version=version, fingerprint=fingerprint,
                                   snapshot_json=json.dumps(report, default=str), captured_by=payload.captured_by.strip())
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return {'status': 'CAPTURED', 'version': version, 'snapshot_id': snapshot.id,
            'note': 'Immutable evidence snapshot; does not imply approval or tests passed'}


@router.get('/releases/{release_key}/baselines/{version}')
def get_baseline(release_key: str, version: int, project_path: str = Query(...), db: Session = Depends(get_db)):
    release = _release(db, project_path, release_key)
    snapshot = db.query(SdlcReleaseSnapshot).filter(SdlcReleaseSnapshot.release_id == release.id,
                                                     SdlcReleaseSnapshot.version == version).first()
    if not snapshot:
        raise HTTPException(404, 'Baseline version not found')
    return json.loads(snapshot.snapshot_json)

@router.get('/releases/{release_key}/compare')
def compare_baselines(release_key: str, from_version: int, to_version: int,
                      project_path: str = Query(...), db: Session = Depends(get_db)):
    release = _release(db, project_path, release_key)
    rows = db.query(SdlcReleaseSnapshot).filter(
        SdlcReleaseSnapshot.release_id == release.id,
        SdlcReleaseSnapshot.version.in_([from_version, to_version])).all()
    by_version = {row.version: json.loads(row.snapshot_json) for row in rows}
    if from_version not in by_version or to_version not in by_version:
        raise HTTPException(404, 'One or both baseline versions do not exist')
    before, after = by_version[from_version], by_version[to_version]
    def tests(report):
        return {(c['jira'], c['test_id'].casefold()): c for c in report['test_cases']}
    old, new = tests(before), tests(after)
    common = old.keys() & new.keys()
    changed = []
    for key in sorted(common):
        a, b = old[key], new[key]
        relevant = ('scenario', 'expected', 'declared_operation', 'sources', 'execution_status', 'execution_evidence')
        fields = [field for field in relevant if a.get(field) != b.get(field)]
        if fields:
            changed.append({'jira': key[0], 'test_id': b['test_id'], 'changed_fields': fields,
                            'before_status': a.get('execution_status'), 'after_status': b.get('execution_status')})
    return {'release_key': release_key, 'from_version': from_version, 'to_version': to_version,
            'jira_added': sorted(set(after['jira_keys']) - set(before['jira_keys'])),
            'jira_removed': sorted(set(before['jira_keys']) - set(after['jira_keys'])),
            'tests_added': [{'jira': key[0], 'test_id': new[key]['test_id']} for key in sorted(new.keys() - old.keys())],
            'tests_removed': [{'jira': key[0], 'test_id': old[key]['test_id']} for key in sorted(old.keys() - new.keys())],
            'tests_changed': changed,
            'note': 'Comparison is between captured snapshots, not working-tree changes'}
