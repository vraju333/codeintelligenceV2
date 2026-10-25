"""Opt-in local Git HEAD polling. Only completed jobs advance the cursor.

Not a GitHub webhook: local polling requires the backend to run and have repo access.
"""
from __future__ import annotations
import logging
import re
import subprocess
import threading
from pathlib import Path
from config import settings
from services.field_impact.analyzer import analyze_project, enrich_field_impact
from services.field_impact.graph_store import persist_field_evidence

log=logging.getLogger(__name__)
_stop=threading.Event()
_thread=None
_lock=threading.Lock()
_status={'state':'DISABLED','revision':None,'error':None,'fields':0,'rag':None,'knowledge':None}
FIELD_DECL=re.compile(r"\b(?:private|public|protected)\s+(?:(?:static|final|transient|volatile)\s+)*(?:[\w<>?,.\[\]]+\s+)+([a-zA-Z_]\w*)\s*(?:[;=,])")

def _git(root:Path,*args):
    return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.PIPE,timeout=30).strip()

def _changes(root:Path,old:str|None,new:str):
    if not old:return [str(p.relative_to(root)).replace('\\','/') for p in root.rglob('*.java') if '.git' not in p.parts]
    return [s for s in _git(root,'diff','--name-only',old,new,'--').splitlines() if s.endswith(('.java','.py','.xlsx','.md','.json','.xml'))]

def _fields(root:Path,paths:list[str]):
    fields=set()
    for relative in paths:
        path=root/relative
        if not path.is_file() or path.suffix not in ('.java','.py') or path.stat().st_size>1_000_000:continue
        source=path.read_text(encoding='utf-8',errors='replace')
        if path.suffix=='.java':fields.update(FIELD_DECL.findall(source))
        else:
            import ast
            try:tree=ast.parse(source)
            except SyntaxError:continue
            fields.update(n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute) and isinstance(n.ctx,ast.Store))
    return sorted(fields)[:settings.FIELD_SYNC_MAX_FIELDS]

def sync_once():
    if not _lock.acquire(blocking=False):return {'state':'RUNNING'}
    try:
        root=Path(settings.JAVA_PROJECT_PATH or '').expanduser().resolve()
        if not root.is_dir() or not (root/'.git').exists():raise RuntimeError('JAVA_PROJECT_PATH must point to an accessible Git repository')
        revision=_git(root,'rev-parse','HEAD')
        previous=_status.get('revision')
        if previous==revision and _status.get('state')=='SYNCED':return dict(_status)
        paths=_changes(root,previous,revision)
        fields=_fields(root,paths)
        _status.update(state='RUNNING',error=None,rag=None,knowledge=None)
        # Each field is independently persisted. Do not mark a revision complete if any fails.
        for field in fields:
            result=enrich_field_impact(analyze_project(str(root),field))
            if result.get('warnings'):raise RuntimeError(f'Parser warnings for {field}: {result["warnings"][:3]}')
            persist_field_evidence(result,root.name,revision)
        # Reuse existing source RAG indexer; it is project-scoped and fingerprint-aware.
        if settings.FIELD_SYNC_RAG_ENABLED and any(p.endswith(('.java','.py')) for p in paths):
            from services.rag.rag_service import RagService
            rag=RagService().index_project()
            _status['rag']={'status':rag.get('status')}
        if settings.FIELD_SYNC_KNOWLEDGE_ENABLED:
            from database import SessionLocal
            from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService
            db=SessionLocal()
            try:
                response=EnterpriseKnowledgeSyncService().sync_all(db,trigger='GIT_REVISION')
                _status['knowledge']={'status':response.get('status')}
            finally:db.close()
        _status.update(state='SYNCED',revision=revision,fields=len(fields),changed_files=len(paths),error=None)
    except Exception as exc:
        log.exception('Field impact Git sync failed')
        _status.update(state='ERROR',error=str(exc))
    finally:_lock.release()
    return dict(_status)

def _loop():
    while not _stop.is_set():
        sync_once()
        _stop.wait(settings.FIELD_SYNC_INTERVAL_SECONDS)

def start():
    global _thread
    if not settings.FIELD_SYNC_ENABLED:return dict(_status)
    if _thread and _thread.is_alive():return dict(_status)
    _stop.clear()
    _thread=threading.Thread(target=_loop,name='field-impact-git-sync',daemon=True)
    _thread.start()
    return {'state':'STARTED'}

def stop():
    _stop.set()
    if _thread:_thread.join(timeout=3)

def status():return dict(_status)
