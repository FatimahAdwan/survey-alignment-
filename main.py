from fastapi import FastAPI, Depends, HTTPException, Header
from routes import survey
import os

app = FastAPI(title="Survey Alignment API")

# # Public endpoints just for testing
# @app.get("/")
# def root():
#     return {"status": "ok", "message": "Survey alignment API is running 🎉"}

# @app.get("/health")
# def health():
#     return {"ok": True}

# Private key for protection
API_KEY = os.getenv("PRIVATE_API_KEY")  
def verify_api_key(x_api_key: str | None = Header(default=None)):
    if API_KEY:                   # only enforce when set
        if x_api_key == API_KEY:
            return True
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return True

# Protect all survey routes with the API key 
app.include_router(survey.router, dependencies=[Depends(verify_api_key)])
