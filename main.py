from sqlalchemy import inspect, text

from fastapi import FastAPI
import logging

from config import settings
from database import engine, initialize_storage, storage_status
# Import ORM models before create_all so a fresh CodeIntelligence database
# creates both scenario and baseline tables correctly.
import db_models  # noqa: F401
import baseline_models  # noqa: F401

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles



def _ensure_scenario_registry_columns():
    """
    Keep existing PostgreSQL/SQLite databases compatible with the current
    Scenario Registry model. This is intentionally additive and never deletes
    scenario or baseline data.
    """
    required_columns = {
        "jira_id": "VARCHAR(100)",
        "request_json": "TEXT",
        "expected_response_json": "TEXT",
        "expected_db_effect": "TEXT",
        "involved_classes": "TEXT",
        "project_path": "TEXT",
    }

    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if "scenarios" not in table_names:
            return

        existing = {column["name"] for column in inspector.get_columns("scenarios")}
        missing = {
            name: sql_type
            for name, sql_type in required_columns.items()
            if name not in existing
        }

        if not missing:
            return

        with engine.begin() as connection:
            for name, sql_type in missing.items():
                connection.execute(
                    text(f"ALTER TABLE scenarios ADD COLUMN {name} {sql_type}")
                )
    except Exception as exc:
        # Startup should give a useful failure rather than silently corrupt data.
        raise RuntimeError(
            f"Unable to upgrade Scenario Registry database schema: {exc}"
        ) from exc





def _ensure_scenario_baseline_columns():
    """Additive upgrade for named Main Baselines."""
    required_columns = {
        "baseline_name": "VARCHAR(150)",
        "release_version": "INTEGER",
    }
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if "scenario_baselines" not in table_names:
            return
        existing = {column["name"] for column in inspector.get_columns("scenario_baselines")}
        with engine.begin() as connection:
            for name, sql_type in required_columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE scenario_baselines ADD COLUMN {name} {sql_type}"))
    except Exception as exc:
        raise RuntimeError(f"Unable to upgrade Main Baseline database schema: {exc}") from exc

def _ensure_testing_baseline_columns():
    """Additive upgrade for periodic testing-baseline metadata."""
    required_columns = {
        "jira_ids": "JSON",
        "baseline_id": "INTEGER",
    }

    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        if "scenario_test_baselines" not in table_names:
            return

        existing = {
            column["name"]
            for column in inspector.get_columns("scenario_test_baselines")
        }
        missing = {
            name: sql_type
            for name, sql_type in required_columns.items()
            if name not in existing
        }
        if not missing:
            return

        with engine.begin() as connection:
            for name, sql_type in missing.items():
                connection.execute(
                    text(
                        f"ALTER TABLE scenario_test_baselines "
                        f"ADD COLUMN {name} {sql_type}"
                    )
                )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to upgrade testing baseline database schema: {exc}"
        ) from exc


initialize_storage()
_ensure_scenario_registry_columns()
_ensure_scenario_baseline_columns()
_ensure_testing_baseline_columns()


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
from routers.jira_knowledge_router import router as jira_knowledge_router
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


@app.get("/api/storage/status")
def get_storage_status():
    """Shows which persistence mode is active without exposing DB credentials."""
    return storage_status()
app.include_router(project_router)

app.include_router(
    jira_impact_router
)
app.include_router(jira_knowledge_router)

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
