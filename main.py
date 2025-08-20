from fastapi import FastAPI, Depends, HTTPException, Header
from routes import survey
import os

app = FastAPI(title="Survey Alignment API")

# --- Public endpoints (useful for testing/health) ---
@app.get("/")
def root():
    return {"status": "ok", "message": "Survey alignment API is running 🎉"}

@app.get("/health")
def health():
    return {"ok": True}

# --- API key gate (optional: only enforced if env var is set) ---
API_KEY = os.getenv("PRIVATE_API_KEY")  # match your Render env var name

def verify_api_key(x_api_key: str | None = Header(default=None)):
    if API_KEY:                   # only enforce when set
        if x_api_key == API_KEY:
            return True
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

# Protect all survey routes with the API key (when set)
app.include_router(survey.router, dependencies=[Depends(verify_api_key)])
