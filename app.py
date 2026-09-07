"""
REST entrypoint for the IBP Demand Planning Agent.
Deployed as its own Cloud Foundry app -- separate route, own manifest,
independent of any other app already in this repo.
"""
import os
from fastapi import FastAPI
from pydantic import BaseModel
from agent.graph import run_agent
app = FastAPI(title="IBP Demand Planning Agent")

class AskRequest(BaseModel):
   message: str

class AskResponse(BaseModel):
   reply: str

@app.get("/health")
def health():
   return {"status": "ok"}

@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
   reply = run_agent(req.message)
   return AskResponse(reply=reply)

if __name__ == "__main__":
   import uvicorn
   port = int(os.environ.get("PORT", 8080))  # CF injects PORT
   uvicorn.run(app, host="0.0.0.0", port=port)