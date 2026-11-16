"""
REST entrypoint for the IBP Demand Planning Agent.
Deployed as its own Cloud Foundry app -- separate route, own manifest,
independent of any other app already in this repo.
"""
import os
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from agent.graph import run_agent_with_data
from agent.s4_capacity_graph import run_s4_agent_with_data
from agent.maintenance_graph import run_maintenance_agent_with_data
from customer_base_agent.graph import run_customer_base_agent
from product_migration_agent.api import router as migration_router

app = FastAPI(title="IBP Demand Planning Agent")
app.include_router(migration_router)

ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
FRONTEND_BUILD_DIR = STATIC_DIR / "build"

# Stream the large use-case videos with byte ranges so browser seeking works.
USE_CASE_VIDEOS = {
   "ingenia_agentic_ai (1).mp4",
   "livanova_agentic_ai (1).mp4",
   "YASH_Master_Data_Utility_MDU_Final (1).mp4",
}

@app.api_route("/static/Usecases/{filename}", methods=["GET", "HEAD"], include_in_schema=False)
def use_case_video(filename: str, request: Request):
   if filename not in USE_CASE_VIDEOS:
      raise HTTPException(status_code=404)
   path = STATIC_DIR / "Usecases" / filename
   if not path.is_file():
      raise HTTPException(status_code=404)
   size = path.stat().st_size
   range_header = request.headers.get("range")
   if not range_header:
      return FileResponse(path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"}, method=request.method)
   try:
      unit, spec = range_header.split("=", 1)
      start_text, end_text = spec.split("-", 1)
      if unit != "bytes" or "," in spec or not (start_text or end_text):
         raise ValueError()
      if not start_text:
         length = int(end_text)
         if length <= 0:
            raise ValueError()
         start, end = max(0, size - length), size - 1
      else:
         start = int(start_text)
         end = min(int(end_text), size - 1) if end_text else size - 1
      if start < 0 or start >= size or end < start:
         raise ValueError()
   except ValueError:
      return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
   headers = {"Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)}
   if request.method == "HEAD":
      return Response(status_code=206, media_type="video/mp4", headers=headers)
   def chunks():
      remaining = end - start + 1
      with path.open("rb") as file:
         file.seek(start)
         while remaining:
            chunk = file.read(min(65536, remaining))
            if not chunk:
               break
            remaining -= len(chunk)
            yield chunk
   return StreamingResponse(chunks(), status_code=206, media_type="video/mp4", headers=headers)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/landing", StaticFiles(directory=STATIC_DIR / "landing", html=True), name="landing")

class AskRequest(BaseModel):
   message: str
   conversation_id: str | None = None

class AskResponse(BaseModel):
   reply: str
   insights: list[dict] = []
   conversation_id: str

# Trial-friendly session store. Replace this with Redis/HANA for persistence
# across restarts or multiple Cloud Foundry instances.
CONVERSATIONS: dict[str, list[dict]] = {}
S4_CONVERSATIONS: dict[str, list[dict]] = {}
MAINTENANCE_CONVERSATIONS: dict[str, list[dict]] = {}
CUSTOMER_BASE_CONVERSATIONS: dict[str, list[dict]] = {}

@app.get("/", include_in_schema=False)
def home():
   return FileResponse(STATIC_DIR / "landing" / "index.html")

@app.get("/dashboard", include_in_schema=False)
def dashboard():
   return FileResponse(FRONTEND_BUILD_DIR / "index.html")

@app.get("/health")
def health():
   return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = CONVERSATIONS.get(conversation_id, [])
   result = run_agent_with_data(req.message, history=history)
   CONVERSATIONS[conversation_id] = history + [
      {"role": "user", "content": req.message},
      {"role": "assistant", "content": [{"type": "text", "text": result["reply"]}]},
   ]
   return AskResponse(**result, conversation_id=conversation_id)

@app.post("/s4/ask", response_model=AskResponse)
def ask_s4(req: AskRequest):
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = S4_CONVERSATIONS.get(conversation_id, [])
   result = run_s4_agent_with_data(req.message, history=history)
   S4_CONVERSATIONS[conversation_id] = history + [
      {"role": "user", "content": req.message},
      {"role": "assistant", "content": [{"type": "text", "text": result["reply"]}]},
   ]
   return AskResponse(**result, conversation_id=conversation_id)

@app.post("/maintenance/ask", response_model=AskResponse)
def ask_maintenance(req: AskRequest):
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = MAINTENANCE_CONVERSATIONS.get(conversation_id, [])
   result = run_maintenance_agent_with_data(req.message, history=history)
   MAINTENANCE_CONVERSATIONS[conversation_id] = history + [
      {"role": "user", "content": req.message},
      {"role": "assistant", "content": [{"type": "text", "text": result["reply"]}]},
   ]
   return AskResponse(**result, conversation_id=conversation_id)

@app.post("/customer-base/ask", response_model=AskResponse)
def ask_customer_base(req: AskRequest):
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = CUSTOMER_BASE_CONVERSATIONS.get(conversation_id, [])
   result = run_customer_base_agent(
      req.message,
      requester=f"scenario-test:{conversation_id}",
      history=history,
   )
   CUSTOMER_BASE_CONVERSATIONS[conversation_id] = history + [
      {"role": "user", "content": req.message},
      {"role": "assistant", "content": [{"type": "text", "text": result["reply"]}]},
   ]
   return AskResponse(**result, conversation_id=conversation_id)

if __name__ == "__main__":
   import uvicorn
   port = int(os.environ.get("PORT", 8080))  # CF injects PORT
   uvicorn.run(app, host="0.0.0.0", port=port)