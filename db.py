import os
from dotenv import load_dotenv
from supabase import create_client, Client
import json

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# Save individual response
def save_response(survey_id, question_id, question_text, answer, theme:str):
    response = supabase.table("responses").insert({
        "survey_id": survey_id,
        "question_id": question_id,
        "question_text": question_text,
        "answer": answer,
        "theme": theme
    }).execute()
    return response


# Initialize progress tracking
def init_survey_progress(
    survey_id: str,
    question_id: str,          
    theme: str,                 
    all_themes: list,
    first_question_text: str    
):
    supabase.table("survey_progress").insert({
        "survey_id": survey_id,
        "current_question_id": question_id,                 # "q1"
        "current_theme": theme,                             # first theme
        "theme_sequence": all_themes,                       # array column
        "completed_themes": [],                             # array column
        # Store TEXTS in history so LLM can avoid repeats
        "question_history": json.dumps([first_question_text]),
        # Start per-theme counter at 1 (first Q already asked)
        "theme_question_counts": json.dumps({theme: 1}),
        # Start global continuous counter at 1
        "total_question_count": 1,
        "completed": False
    }).execute()




# Update progress with new question + theme
def update_survey_progress(survey_id: str, next_question_id: str, theme: str):
    result = supabase.table("survey_progress").select("*").eq("survey_id", survey_id).single().execute()
    existing = result.data

    if not existing:
        return

    # Safely decode existing list or fallback to empty list
    try:
        question_history = json.loads(existing.get("question_history", "[]"))
    except json.JSONDecodeError:
        question_history = []

    question_history.append(next_question_id)

    supabase.table("survey_progress").update({
        "question_history": json.dumps(question_history),
        "current_question_id": next_question_id,
        "current_theme": theme
    }).eq("survey_id", survey_id).execute()


# Mark survey complete
def mark_survey_complete(survey_id: str):
    supabase.table("survey_progress").update({"completed": True}).eq("survey_id", survey_id).execute()
