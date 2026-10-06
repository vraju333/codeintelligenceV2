from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from services.engineering_assistant.engineering_assistant_service import EngineeringAssistantService

router = APIRouter(prefix="/api/engineering-assistant", tags=["AI Engineering Assistant"])
service = EngineeringAssistantService()


@router.get("/status")
def engineering_assistant_status():
    return service.status()


@router.get("/current-change")
def engineering_assistant_current_change(db: Session = Depends(get_db)):
    try:
        return service.analyse_current_change(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
