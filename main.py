from fastapi import FastAPI
import logging

from config import settings
from database import Base, engine
# Import ORM models before create_all so a fresh CodeIntelligence database
# creates both scenario and baseline tables correctly.
import db_models  # noqa: F401
import baseline_models  # noqa: F401

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


Base.metadata.create_all(
    bind=engine
)


from routers.project_router import router as project_router

from routers.scanner_router import (
    router as scanner_router,
    service as scanner_service
)

from routers.investigation_router import router as investigation_router
from routers.scenario_router import (
    router as scenario_router
)

from routers.rag_router import (
    router as rag_router,
    rag_service
)

from routers.code_flow_router import (
    router as code_flow_router
)

from routers.endpoint_flow_router import (
    router as endpoint_flow_router
)

from routers.attribute_lineage_router import (
    router as attribute_lineage_router
)

from routers.scenario_attribute_trace_router import (
    router as scenario_attribute_trace_router
)

from routers.defect_comparison_router import (
    router as defect_comparison_router
)

from routers.scenario_baseline_router import (
    router as scenario_baseline_router
)

from seed_data import seed_scenarios

from routers.regression_router import (
    router as regression_router
)

from routers.historical_regression_router import (
    router as historical_regression_router
)

from routers.flowchart_router import (
    router as flowchart_router
)

from routers.regression_report_router import (
    router as regression_report_router
)

from routers.excel_report_router import (
    router as excel_report_router
)
from routers.jira_impact_router import router as jira_impact_router
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION
)

BASE_DIR = Path(__file__).resolve().parent

app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "static"
    ),
    name="static"
)

@app.get("/")
def dashboard():
    return FileResponse(
        BASE_DIR
        / "templates"
        / "index.html"
    )
app.include_router(project_router)

app.include_router(
    jira_impact_router
)

app.include_router(
    scenario_router
)

app.include_router(
    scanner_router
)

app.include_router(
    rag_router
)

app.include_router(
    code_flow_router
)

app.include_router(
    endpoint_flow_router
)

app.include_router(
    attribute_lineage_router
)

app.include_router(
    scenario_attribute_trace_router
)

app.include_router(
    defect_comparison_router
)

app.include_router(investigation_router)

app.include_router(
    scenario_baseline_router
)

app.include_router(
    regression_router
)

app.include_router(
    historical_regression_router
)

app.include_router(
    flowchart_router
)

app.include_router(
    regression_report_router
)
app.include_router(
    excel_report_router
)
@app.on_event("startup")
def startup():

    seed_scenarios()

    initialization = {
        "enabled": settings.AUTO_PROJECT_INITIALIZATION,
        "status": "SKIPPED",
        "scan": None,
        "rag": None,
        "error": None
    }

    if settings.AUTO_PROJECT_INITIALIZATION:
        try:
            scan_result = scanner_service.scan()
            initialization["scan"] = {
                "project_path": scan_result.project_path,
                "total_java_files": scan_result.total_java_files,
                "total_classes": len(scan_result.classes)
            }

            rag_result = rag_service.index_project()
            initialization["rag"] = rag_result
            initialization["status"] = "READY"

            logger.info(
                "Project initialization completed: %s Java files, %s RAG chunks",
                scan_result.total_java_files,
                rag_result.get("chunks")
            )

        except Exception as exc:
            initialization["status"] = "ERROR"
            initialization["error"] = str(exc)
            logger.exception("Automatic project initialization failed")

    app.state.project_initialization = initialization


@app.get("/health")
def health():

    initialization = getattr(
        app.state,
        "project_initialization",
        {
            "enabled": settings.AUTO_PROJECT_INITIALIZATION,
            "status": "NOT_STARTED",
            "scan": None,
            "rag": None,
            "error": None
        }
    )

    return {
        "status": "UP",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "java_project_path": settings.JAVA_PROJECT_PATH,
        "project_initialization": initialization
    }
