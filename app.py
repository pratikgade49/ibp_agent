"""
REST entrypoint for the IBP Demand Planning Agent.
Deployed as its own Cloud Foundry app -- separate route, own manifest,
independent of any other app already in this repo.
"""
import os
import json
import uuid
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from agent.graph import run_agent_with_data
from agent.s4_capacity_graph import MAX_S4_HISTORY_MESSAGES, run_s4_agent_with_data
from agent.maintenance_graph import MAX_HISTORY_MESSAGES, run_maintenance_agent_with_data
from customer_base_agent.graph import run_customer_base_agent
from product_migration_agent.api import router as migration_router
from ewm_queue_agent.api import router as ewm_router
from rag.sharepoint_knowledge import start_sync_worker
from rag.memory import configured_store
from rag.summaries import refresh_summary
from auth.store import AuthStore
from auth.routes import install_auth

app = FastAPI(title="IBP Demand Planning Agent")
app.include_router(migration_router)
app.include_router(ewm_router)
install_auth(app)
MEMORY = None

@app.on_event("startup")
def start_knowledge_sync():
   global MEMORY
   MEMORY = configured_store()
   if os.environ.get('AUTH_ENABLED', 'true').lower() == 'true':
      if MEMORY is None:
         raise RuntimeError('Authentication requires a configured memory database')
      app.state.auth = AuthStore(MEMORY)
      app.state.auth.bootstrap(os.environ.get('AUTH_ADMIN_USERNAME', ''), os.environ.get('AUTH_ADMIN_PASSWORD', ''))
   start_sync_worker()

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
   memory_turn_id: str | None = None
   memory_context: dict | None = None

# Trial-friendly session store. Replace this with Redis/HANA for persistence
# across restarts or multiple Cloud Foundry instances.
CONVERSATIONS: dict[str, list[dict]] = {}
S4_CONVERSATIONS: dict[str, list[dict]] = {}
MAINTENANCE_CONVERSATIONS: dict[str, list[dict]] = {}
CUSTOMER_BASE_CONVERSATIONS: dict[str, list[dict]] = {}

def _identity(request: Request) -> str:
   principal = getattr(request.state, 'user', None)
   if not principal or not principal.get('username'):
      raise HTTPException(status_code=401, detail='An authenticated user is required for conversation memory.')
   return principal['username']

def _history(agent: str, conversation_id: str, fallback: dict, question: str, *, username: str) -> list[dict]:
   if not MEMORY:
      return fallback.get((username, conversation_id), [])
   history = MEMORY.history(agent, conversation_id, username=username)
   try:
      summary = refresh_summary(MEMORY, agent, username=username)
   except Exception as exc:
      logging.getLogger(__name__).warning('History summary unavailable (%s)', type(exc).__name__)
      summary = MEMORY.cached_summary(username, agent)
   if summary:
      history.append({"role": "user", "content": "YOUR RECORDED CONVERSATION SUMMARY (automatically supplied to inform the current request; condensed and may omit details): " + json.dumps(summary, ensure_ascii=False)})
   references = MEMORY.related_older_turns(agent, conversation_id, question, username=username)
   if references:
      history.append({"role": "user", "content": "YOUR RECORDED CONVERSATION EXCERPTS (actual earlier exchanges, supplied to inform the current request): " + json.dumps(references, ensure_ascii=False)})
   return history

def _save(agent: str, conversation_id: str, message: str, reply: str, history: list, fallback: dict, *, username: str) -> str | None:
   if MEMORY:
      return MEMORY.save_turn(agent, conversation_id, message, reply, username=username)
   else:
      fallback[(username, conversation_id)] = history + [
         {"role": "user", "content": message},
         {"role": "assistant", "content": [{"type": "text", "text": reply}]},
      ]

def _context_report(history: list[dict], agent: str, result: dict) -> dict:
   """Describe only context forwarded to the graph; do not claim it was used."""
   if result.pop('_history_forwarded', True) is False:
      return {'status': 'not_forwarded', 'recent_turns': 0, 'summary_turns': 0, 'excerpts': []}
   limits = {'s4': MAX_S4_HISTORY_MESSAGES, 'maintenance': MAX_HISTORY_MESSAGES, 'scenario': 6}
   visible = history[-limits[agent]:] if agent in limits else history
   recent = 0
   summary_turns = 0
   summary_refresh_failed = False
   excerpts = []
   for entry in visible:
      text = entry.get('content')
      if not isinstance(text, str):
         continue
      if text.startswith('YOUR RECORDED CONVERSATION SUMMARY '):
         try:
            data = json.loads(text.split(': ', 1)[1])
            summary_turns = data.get('turn_count', 0)
            summary_refresh_failed = bool(data.get('refresh_failed'))
         except (ValueError, IndexError, TypeError):
            pass
      elif text.startswith('YOUR RECORDED CONVERSATION EXCERPTS '):
         try:
            excerpts = [{'date': item.get('date', ''), 'source': item.get('source', ''), 'question': item.get('question', '')} for item in json.loads(text.split(': ', 1)[1])]
         except (ValueError, IndexError, TypeError, AttributeError):
            pass
      elif entry.get('role') == 'user':
         recent += 1
   return {'status': 'provided' if recent or summary_turns or excerpts else 'none', 'recent_turns': recent, 'summary_turns': summary_turns, 'summary_refresh_failed': summary_refresh_failed, 'excerpts': excerpts}

class MemoryTurn(BaseModel):
   agent: str
   conversation_id: str = Field(min_length=1, max_length=128)
   message: str = Field(min_length=1, max_length=20000)
   reply: str = Field(min_length=1, max_length=20000)

class Feedback(BaseModel):
   turn_id: str = Field(min_length=1, max_length=128)
   rating: str
   correction: str = Field(default='', max_length=5000)

@app.post('/memory/feedback')
def submit_feedback(body: Feedback, request: Request):
   if not MEMORY:
      raise HTTPException(status_code=503, detail='Memory is unavailable.')
   principal = getattr(request.state, 'user', None)
   if not principal:
      raise HTTPException(status_code=401, detail='Please sign in.')
   try:
      MEMORY.add_feedback(body.turn_id, principal['username'], body.rating, body.correction)
   except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc)) from exc
   return {'saved': True}

@app.get('/auth/feedback')
def review_feedback(request: Request):
   if not MEMORY:
      raise HTTPException(status_code=503, detail='Memory is unavailable.')
   return {'feedback': MEMORY.list_feedback(_identity(request))}

@app.post("/memory/turn", include_in_schema=False)
def save_client_turn(turn: MemoryTurn, request: Request):
   username = _identity(request)
   if turn.agent not in {"ewm", "migration"}:
      raise HTTPException(status_code=400, detail="Unsupported agent")
   if MEMORY:
      MEMORY.save_turn(turn.agent, turn.conversation_id, turn.message, turn.reply, username=username)
   return {"saved": bool(MEMORY)}

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
def ask(req: AskRequest, request: Request):
   username = _identity(request)
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = _history("ibp", conversation_id, CONVERSATIONS, req.message, username=username)
   result = run_agent_with_data(req.message, history=history)
   context = _context_report(history, 'ibp', result)
   turn_id = _save("ibp", conversation_id, req.message, result["reply"], history, CONVERSATIONS, username=username)
   return AskResponse(**result, conversation_id=conversation_id, memory_turn_id=turn_id, memory_context=context)

@app.post("/s4/ask", response_model=AskResponse)
def ask_s4(req: AskRequest, request: Request):
   username = _identity(request)
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = _history("s4", conversation_id, S4_CONVERSATIONS, req.message, username=username)
   result = run_s4_agent_with_data(req.message, history=history)
   context = _context_report(history, 's4', result)
   turn_id = _save("s4", conversation_id, req.message, result["reply"], history, S4_CONVERSATIONS, username=username)
   return AskResponse(**result, conversation_id=conversation_id, memory_turn_id=turn_id, memory_context=context)

@app.post("/maintenance/ask", response_model=AskResponse)
def ask_maintenance(req: AskRequest, request: Request):
   username = _identity(request)
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = _history("maintenance", conversation_id, MAINTENANCE_CONVERSATIONS, req.message, username=username)
   result = run_maintenance_agent_with_data(req.message, history=history)
   context = _context_report(history, 'maintenance', result)
   turn_id = _save("maintenance", conversation_id, req.message, result["reply"], history, MAINTENANCE_CONVERSATIONS, username=username)
   return AskResponse(**result, conversation_id=conversation_id, memory_turn_id=turn_id, memory_context=context)

@app.post("/customer-base/ask", response_model=AskResponse)
def ask_customer_base(req: AskRequest, request: Request):
   username = _identity(request)
   conversation_id = req.conversation_id or str(uuid.uuid4())
   history = _history("scenario", conversation_id, CUSTOMER_BASE_CONVERSATIONS, req.message, username=username)
   result = run_customer_base_agent(
      req.message,
      requester=f"{username}:{conversation_id}",
      history=history,
   )
   context = _context_report(history, 'scenario', result)
   turn_id = _save("scenario", conversation_id, req.message, result["reply"], history, CUSTOMER_BASE_CONVERSATIONS, username=username)
   return AskResponse(**result, conversation_id=conversation_id, memory_turn_id=turn_id, memory_context=context)

if __name__ == "__main__":
   import uvicorn
   port = int(os.environ.get("PORT", 8080))  # CF injects PORT
   uvicorn.run(app, host="0.0.0.0", port=port)
