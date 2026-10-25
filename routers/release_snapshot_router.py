"""Project-scoped release preview/finalization; no manual scenario baseline required."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database import get_db
from db_models import JiraKnowledge, KnowledgeDocument, MappingDocument
from sdlc_models import SdlcRelease, SdlcTestExecution
from release_snapshot_models import ReleaseManifest
from services.agent.sdlc_report_service import scope

router = APIRouter(prefix='/api/release-snapshots', tags=['Release Snapshots'])

class FinalizeRequest(BaseModel):
    project_path: str
    release_key: str
    finalized_by: str = Field(min_length=1)
    expected_git_sha: str = Field(min_length=40, max_length=40)


def _release(db, project, key):
    if not project.strip() or not key.strip():
        raise HTTPException(400, 'Project and release key are required')
    rows = db.query(SdlcRelease).filter(SdlcRelease.release_key == key).all()
    release = next((r for r in rows if scope(r.project_path) == scope(project)), None)
    if not release:
        raise HTTPException(404, 'Create the release and explicitly associate JIRAs using POST /api/sdlc/releases first')
    return release


def _git(project):
    root = Path(project).resolve()
    if not root.is_dir():
        raise HTTPException(400, 'Indexed project directory is not accessible')
    def cmd(*args):
        try:
            return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=12, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise HTTPException(400, 'Cannot resolve local Git revision for project')
    top = Path(cmd('rev-parse', '--show-toplevel')).resolve()
    if top != root:
        raise HTTPException(400, 'Project must point to the Git repository root')
    return {'branch': cmd('rev-parse', '--abbrev-ref', 'HEAD'), 'commit_sha': cmd('rev-parse', 'HEAD'),
            'repository_root': str(top), 'working_tree_clean': not bool(cmd('status', '--porcelain'))}


def _build(db, release):
    project = release.project_path
    keys = sorted(set(json.loads(release.jira_keys_json or '[]')))
    jira_rows = db.query(JiraKnowledge).filter(JiraKnowledge.jira_id.in_(keys)).all() if keys else []
    jira = [{'jira_id': r.jira_id, 'title': r.title, 'requirement': r.requirement,
             'updated_at': str(r.updated_at)} for r in jira_rows if r.project_path and scope(r.project_path) == scope(project)]
    missing = sorted(set(keys) - {r['jira_id'] for r in jira})
    documents = db.query(KnowledgeDocument).all()
    knowledge = [{'id': d.id, 'title': d.title, 'source_type': d.source_type,
                  'source_ref': d.source_ref, 'content': d.content, 'metadata_json': d.metadata_json,
                  'updated_at': str(d.updated_at)} for d in documents
                 if scope(d.project_path) == scope(project) and d.status == 'ACTIVE']
    mappings = [{'id': m.id, 'filename': m.filename, 'title': m.title, 'document_version': m.document_version,
                 'checksum_sha256': m.checksum_sha256, 'source_ref': m.source_ref,
                 'sheet_count': m.sheet_count, 'row_count': m.row_count}
                for m in db.query(MappingDocument).all()
                if scope(m.project_path) == scope(project) and m.status == 'ACTIVE']
    runs = [{'jira_key': e.jira_key, 'test_id': e.test_id, 'status': e.status,
             'git_revision': e.git_revision, 'evidence_ref': e.evidence_ref,
             'executed_at': str(e.executed_at), 'recorded_by': e.recorded_by,
             'evidence_type': 'USER_RECORDED_NOT_INDEPENDENTLY_VERIFIED'}
            for e in db.query(SdlcTestExecution).all()
            if scope(e.project_path) == scope(project) and e.jira_key in keys]
    git = _git(project)
    return {'schema_version': 1, 'release_key': release.release_key, 'project_path': project,
            'git': git, 'jira_keys': keys, 'jira_documents': jira, 'missing_jira_documents': missing,
            'knowledge_documents': knowledge, 'mapping_documents': mappings,
            'recorded_test_executions': runs,
            'limitations': ['Only explicitly associated release JIRAs are included',
                            'Active project-scoped knowledge and mapping documents are captured; release-specific document membership is not inferred',
                            'Mapping workbook content must remain available by checksum; this manifest preserves metadata, not workbook bytes',
                            'Recorded test execution is not independently verified',
                            'No CI provider or Neo4j graph snapshot is included']}


@router.get('/{release_key}/preview')
def preview(release_key: str, project_path: str = Query(...), db: Session = Depends(get_db)):
    release = _release(db, project_path, release_key)
    manifest = db.query(ReleaseManifest).filter_by(release_id=release.id).first()
    if manifest:
        return {'status': 'FINALIZED', 'manifest': json.loads(manifest.manifest_json),
                'manifest_sha256': manifest.manifest_sha256}
    return {'status': 'DRAFT', 'manifest': _build(db, release)}


@router.post('/finalize', status_code=201)
def finalize(payload: FinalizeRequest, db: Session = Depends(get_db)):
    release = _release(db, payload.project_path, payload.release_key)
    if db.query(ReleaseManifest).filter_by(release_id=release.id).first():
        raise HTTPException(409, 'Release already finalized; finalized snapshots cannot be overwritten')
    manifest = _build(db, release)
    if not re.fullmatch('[0-9a-fA-F]{40}', payload.expected_git_sha):
        raise HTTPException(400, 'expected_git_sha must be a full Git commit SHA')
    if manifest['git']['commit_sha'] != payload.expected_git_sha.lower():
        raise HTTPException(409, 'Git HEAD changed since preview; reload preview before finalizing')
    if not manifest['git']['working_tree_clean']:
        raise HTTPException(409, 'Working tree has uncommitted changes; commit or clean before finalizing')
    if manifest['missing_jira_documents']:
        raise HTTPException(409, 'Some release JIRAs have no project-scoped saved requirement: '+', '.join(manifest['missing_jira_documents']))
    manifest['finalized_at'] = datetime.now(timezone.utc).isoformat()
    manifest['finalized_by'] = payload.finalized_by.strip()
    content = json.dumps(manifest, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    row = ReleaseManifest(release_id=release.id, manifest_sha256=digest, manifest_json=content,
                          finalized_by=payload.finalized_by.strip())
    db.add(row)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(409, 'Release already finalized or persistence failed')
    return {'status': 'FINALIZED', 'release_key': release.release_key, 'manifest_id': row.id,
            'manifest_sha256': digest, 'git_sha': manifest['git']['commit_sha']}


@router.get('/{release_key}')
def read_snapshot(release_key: str, project_path: str = Query(...), db: Session = Depends(get_db)):
    release = _release(db, project_path, release_key)
    row = db.query(ReleaseManifest).filter_by(release_id=release.id).first()
    if not row:
        raise HTTPException(404, 'Release is not finalized')
    content = row.manifest_json
    return {'status': 'FINALIZED', 'manifest_sha256': row.manifest_sha256,
            'integrity_ok': hashlib.sha256(content.encode('utf-8')).hexdigest() == row.manifest_sha256,
            'manifest': json.loads(content)}
