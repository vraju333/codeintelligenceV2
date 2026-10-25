from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy.orm import Session

from database import get_db
from services.regression.git_diff_service import GitDiffService
from services.regression.regression_impact_service import (
    RegressionImpactService
)
from services.test_analysis.test_code_analysis_service import TestCodeAnalysisService
from services.release_intelligence.release_intelligence_service import ReleaseIntelligenceService


router = APIRouter(
    prefix="/api/regression",
    tags=["Regression Analysis"]
)

service = RegressionImpactService()


@router.get("/git-changes")
def get_git_changes(mode: str = "working", base_ref: str | None = None, target_ref: str | None = None):

    try:

        return (
            GitDiffService()
            .analyse_changes(mode=mode, base_ref=base_ref, target_ref=target_ref)
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )


@router.get("/impact")
def analyse_regression_impact(
    mode: str = "working", base_ref: str | None = None, target_ref: str | None = None,
    db: Session = Depends(get_db)
):

    try:

        return RegressionImpactService().analyse(db, mode=mode, base_ref=base_ref, target_ref=target_ref)

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )

@router.get("/test-analysis")
def analyse_test_code():
    try:
        return TestCodeAnalysisService().analyse()
    except RuntimeError as exception:
        raise HTTPException(status_code=400, detail=str(exception))


@router.get("/release-intelligence")
def analyse_release_intelligence(mode: str = "working", base_ref: str | None = None, target_ref: str | None = None, db: Session = Depends(get_db)):
    try:
        return ReleaseIntelligenceService().analyse(db, mode=mode, base_ref=base_ref, target_ref=target_ref)
    except RuntimeError as exception:
        raise HTTPException(status_code=400, detail=str(exception))
