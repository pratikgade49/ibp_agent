"""
REST entrypoint for the IBP Demand Planning Agent.
Deployed as its own Cloud Foundry app -- separate route, own manifest,
independent of any other app already in this repo.
"""
import os
import uuid
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from agent.graph import run_agent_with_data
app = FastAPI(title="IBP Demand Planning Agent")

app.mount("/static", StaticFiles(directory="static"), name="static")

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

@app.get("/", include_in_schema=False)
def home():
   return FileResponse("static/index.html")

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

if __name__ == "__main__":
   import uvicorn
   port = int(os.environ.get("PORT", 8080))  # CF injects PORT
   uvicorn.run(app, host="0.0.0.0", port=port)