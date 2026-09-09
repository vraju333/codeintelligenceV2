from fastapi import (
    APIRouter,
    Depends,
    HTTPException
)
from sqlalchemy.orm import Session

from database import get_db
from services.regression.regression_impact_service import (
    RegressionImpactService
)


router = APIRouter(
    prefix="/api/regression",
    tags=["Regression Analysis"]
)

service = RegressionImpactService()


@router.get("/git-changes")
def get_git_changes():

    try:

        return (
            service.git_diff_service
            .analyse_changes()
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )


@router.get("/impact")
def analyse_regression_impact(
    db: Session = Depends(get_db)
):

    try:

        return service.analyse(
            db
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )