from fastapi import APIRouter, HTTPException

from services.jira.jira_live_client import jira_live_client


router = APIRouter(prefix="/api/integrations/jira", tags=["Live JIRA"])


@router.get("/issues/{issue_key}")
def get_live_jira_issue(issue_key: str):
    try:
        return {
            "status": "LIVE",
            "issue": jira_live_client.get_issue(issue_key),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
