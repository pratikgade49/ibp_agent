"""FastAPI router. Mounted by app.py with `app.include_router(migration_router)`."""
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from . import config
from .service import JobStore, convert_files

log = logging.getLogger(__name__)
router = APIRouter(prefix="/migration", tags=["product-migration"])
JOBS = JobStore()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.post("/convert")
async def convert_endpoint(
    data: UploadFile = File(...),
    mapping: UploadFile = File(...),
    rules: UploadFile = File(...),
    min_confidence: float | None = Form(None),
    correction: str | None = Form(None),
):
    blobs = [await f.read(config.MAX_UPLOAD_BYTES + 1) for f in (data, mapping, rules)]
    try:
        result = await run_in_threadpool(convert_files, blobs[0], blobs[1], blobs[2], min_confidence, None, None, correction)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 - typically an LLM/provider failure
        log.exception("product migration failed")
        raise HTTPException(status_code=502, detail=f"Conversion failed: {e}") from e
    job_id = JOBS.put(result.workbook)
    summary = dict(result.summary)
    preview = summary.pop("preview", [])
    return {"job_id": job_id, "summary": summary, "human_needed": result.human_needed,
            "download_url": f"/migration/download/{job_id}", "preview": preview}


@router.get("/download/{job_id}")
def download(job_id: str):
    wb = JOBS.get(job_id)
    if wb is None:
        raise HTTPException(status_code=404, detail="Result not found or expired. Run the conversion again.")
    return Response(wb, media_type=XLSX, headers={"Content-Disposition": 'attachment; filename="S4_Product_Master_Converted.xlsx"'})
