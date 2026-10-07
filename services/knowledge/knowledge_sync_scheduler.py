from __future__ import annotations
import logging
import threading
from config import settings
from database import SessionLocal
from services.knowledge.enterprise_knowledge_sync_service import EnterpriseKnowledgeSyncService

logger=logging.getLogger(__name__)
_stop=threading.Event(); _thread:threading.Thread|None=None

def _loop():
    service=EnterpriseKnowledgeSyncService()
    while not _stop.is_set():
        db=SessionLocal()
        try:
            service.ensure_default_sources(db)
            service.sync_all(db,trigger="SCHEDULED")
        except Exception:
            logger.exception("Automatic knowledge synchronization failed")
        finally: db.close()
        _stop.wait(settings.KNOWLEDGE_SYNC_INTERVAL_SECONDS)

def start_knowledge_sync_scheduler():
    global _thread
    if not settings.KNOWLEDGE_AUTO_SYNC_ENABLED: return {"status":"DISABLED"}
    if _thread and _thread.is_alive(): return {"status":"RUNNING"}
    _stop.clear(); _thread=threading.Thread(target=_loop,name="codeintelligence-knowledge-sync",daemon=True); _thread.start()
    return {"status":"STARTED","interval_seconds":settings.KNOWLEDGE_SYNC_INTERVAL_SECONDS}

def stop_knowledge_sync_scheduler():
    _stop.set()
