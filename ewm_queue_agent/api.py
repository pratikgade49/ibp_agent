"""Queue monitoring API: no queue modifications are exposed."""
import logging
from datetime import date
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import requests

from .service import fetch_queues
from .interpret import interpret_request
from .community import find_solutions

router = APIRouter(prefix="/api/ewm", tags=["ewm-queue"])
log = logging.getLogger(__name__)


@router.get("/queues")
def queues(warehouse: str = Query(default="", max_length=4), date_from: date | None = None, date_to: date | None = None):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="The start date must be on or before the end date.")
    try:
        return fetch_queues(warehouse.strip(), date_from, date_to)
    except RuntimeError as exc:
        log.warning("EWM queue lookup failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Unable to retrieve EWM queues: {exc}") from exc
    except requests.RequestException as exc:
        log.warning("EWM queue connection failed: %s", exc)
        raise HTTPException(status_code=502, detail="Unable to connect to the SAP queue service. Check the SAP connection and try again.") from exc


class InterpretRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    warehouse: str = Field(default="", max_length=4)
    today: date


class CommunityRequest(BaseModel):
    error: str = Field(default="", max_length=300)
    msgid: str = Field(default="", max_length=20)
    msgno: str = Field(default="", max_length=3)


@router.post("/community")
def community(body: CommunityRequest):
    return find_solutions(body.error, body.msgid, body.msgno)


@router.post("/interpret")
def interpret(body: InterpretRequest):
    try:
        return interpret_request(body.message, body.warehouse, body.today)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
