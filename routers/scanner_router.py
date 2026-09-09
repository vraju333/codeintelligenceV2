from fastapi import APIRouter

from schemas import ScanResponse
from services.scanner.java_scanner_service import JavaScannerService


router = APIRouter(
    prefix="/api/code",
    tags=["Code Analysis"]
)

service = JavaScannerService()


@router.post("/scan", response_model=ScanResponse)
def scan_java_project():
    return service.scan()
