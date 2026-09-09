from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Depends
)

from services.lineage.attribute_lineage_service import (
    AttributeLineageService
)
from services.lineage.attribute_impact_service import AttributeImpactService
from database import get_db
from sqlalchemy.orm import Session


router = APIRouter(
    prefix="/api/attribute-lineage",
    tags=["Attribute Lineage"]
)


@router.get("/analyze")
def analyze_attribute(
    attribute: str = Query(...)
):

    try:

        service = (
            AttributeLineageService()
        )

        return service.analyze(
            attribute_name=attribute
        )

    except RuntimeError as exception:

        raise HTTPException(
            status_code=400,
            detail=str(exception)
        )

    except Exception as exception:

        raise HTTPException(
            status_code=500,
            detail=str(exception)
        )

@router.get("/impact")
def analyze_attribute_impact(
    attribute: str = Query(...),
    db: Session = Depends(get_db)
):
    try:
        return AttributeImpactService().analyze(attribute, db)
    except RuntimeError as exception:
        raise HTTPException(status_code=400, detail=str(exception))
    except Exception as exception:
        raise HTTPException(status_code=500, detail=str(exception))
