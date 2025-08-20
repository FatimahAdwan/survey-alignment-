from fastapi import FastAPI, Depends, HTTPException, Header
from routes import survey
import os

app = FastAPI()

app.include_router(survey.router)

# API key gate 
REPORT_API_KEY = os.getenv("REPORT_API_KEY")  

def verify_api_key(x_api_key: str = Header(None)):
    # if you set a key in the environment, require it
    if REPORT_API_KEY:
        if x_api_key == REPORT_API_KEY:
            return True
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    # if no key is set (e.g., local dev), allow all
    return True

# protect all routes (you can limit this to /analysis only if you want)
app.include_router(survey.router, dependencies=[Depends(verify_api_key)])
