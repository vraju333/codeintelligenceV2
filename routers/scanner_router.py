from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db

from schemas import ScanResponse
from services.scanner.java_scanner_service import JavaScannerService
from services.scenario.scenario_service import ScenarioService


router = APIRouter(
    prefix="/api/code",
    tags=["Code Analysis"]
)

service = JavaScannerService()
scenario_service = ScenarioService()


@router.post("/scan")
def scan_java_project(db: Session = Depends(get_db)):
    result = service.scan()
    scenario_sync = scenario_service.sync_discovered_operations(db)
    payload = result.model_dump()
    payload["scenario_sync"] = scenario_sync
    return payload
