import re
import difflib
from fastapi import APIRouter, HTTPException
from models import StartSurveyRequest, QuestionResponse, AnswerRequest, FollowUpResponse
from services.llm_logic import generate_first_question, generate_next_question
from db import init_survey_progress, update_survey_progress, supabase
from services.analysis import build_company_report

router = APIRouter()

def _normalize_company(name: str) -> str:
    s = (name or "").lower()
    # keep only letters/numbers; remove spaces/punctuation so variations match
    return re.sub(r"[^a-z0-9]+", "", s)


def resolve_company_token(company_name: str) -> str:
    """
    Fuzzy-map the typed company name to an existing company_token seen in `surveys`.
    - Uses a normalized form for matching.
    - Returns the stored canonical name if a close match exists.
    - Otherwise, saves the new typed name as canonical.
    """
    
    def _normalize(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (name or "").lower())

    candidate_norm = _normalize(company_name)

    # Pull all existing company_token values
    existing_rows = (
        supabase.table("surveys")
        .select("company_token")
        .execute()
    ).data or []

    existing_tokens = sorted({
        row.get("company_token") for row in existing_rows if row.get("company_token")
    })

    if not existing_tokens:
        return company_name.strip().title()  # first entry, keep original as canonical

    norm_map = {tok: _normalize(tok) for tok in existing_tokens}

    # Exact normalized match
    for tok, norm in norm_map.items():
        if norm == candidate_norm:
            return tok  # return the original stored (canonical) token

        # Handle acronym / abbreviation mapping (e.g., GTB -> Guaranty Trust Bank)
    def is_acronym_match(short: str, long: str) -> bool:
        # Build acronym from longer string
        long_acronym = "".join(word[0] for word in re.findall(r"[A-Za-z]+", long))
        return short.lower() == long_acronym.lower()

    # Try exact acronym match
    for tok in existing_tokens:
        if is_acronym_match(candidate_norm, tok) or is_acronym_match(tok, candidate_norm):
            return tok  # map GTB → Guaranty Trust Bank

    # Fuzzy normalized match
    best_norm = difflib.get_close_matches(candidate_norm, list(norm_map.values()), n=1, cutoff=0.80)
    if best_norm:
        for tok, norm in norm_map.items():
            if norm == best_norm[0]:
                return tok  # return canonical stored token

    # If no match, use typed input as canonical
    return company_name.strip()

@router.post("/start-survey", response_model=QuestionResponse)
def start_survey(data: StartSurveyRequest):
    print("📥 Incoming start-survey payload:", data.dict())
    print("📥 Raw model:", data)

    # company_name -> company_token (temporary: direct pass-through)
    company_token = resolve_company_token(data.company_name)

    # Save to Supabase and capture inserted ID
    response = supabase.table("surveys").insert({
        "full_name": data.full_name,
        "email": data.email,
        "company_token": company_token,   # stored internally
        "role": data.role,
        "business_area": data.business_area,
        "goals": data.goals
    }).execute()

    survey_id = response.data[0]["id"]

    # Defining full theme sequence (consistent casing)
    themes = ["Clarity of Goals", "Measurement of Progress", "Visibility of Reports", "Frontline Impact", "Priority Ranking"]

    first_question_id = "q1"
    first_question_text = "Do you know these goals?"

    # Initialize survey progress with first question and full theme sequence
    init_survey_progress(
        survey_id=survey_id,
        question_id=first_question_id,
        theme=themes[0],
        all_themes=themes,
        first_question_text=first_question_text
    )

    # Generate first question
    first_q = generate_first_question(data, survey_id)
    return first_q

@router.post("/answer", response_model=FollowUpResponse)
def answer_question(data: AnswerRequest):
    return generate_next_question(data)

@router.get("/progress/{survey_id}")
def get_progress(survey_id: str):
    # Load progress state
    prog_res = (
        supabase.table("survey_progress")
        .select("*")
        .eq("survey_id", survey_id)
        .single()
        .execute()
    )
    if not prog_res.data:
        raise HTTPException(status_code=404, detail="Survey progress not found")

    progress = prog_res.data

    # Load a bit of context (role, dept, goals) for the UI
    survey_res = (
        supabase.table("surveys")
        .select("role, business_area, goals")
        .eq("id", survey_id)
        .single()
        .execute()
    )
    survey_ctx = survey_res.data or {}

    # Normalize JSON fields (they might be jsonb or json-encoded strings)
    def ensure_list(v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                import json
                return json.loads(v)
            except Exception:
                return []
        return v

    def ensure_dict(v):
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                import json
                return json.loads(v)
            except Exception:
                return {}
        return v

    theme_sequence = ensure_list(progress.get("theme_sequence"))
    completed_themes = ensure_list(progress.get("completed_themes"))
    question_history = ensure_list(progress.get("question_history"))
    theme_question_counts = ensure_dict(progress.get("theme_question_counts"))

    current_theme = progress.get("current_theme")
    completed = bool(progress.get("completed", False))
    total_question_count = int(progress.get("total_question_count", 0))

    #  Convenience fields for the frontend consumption
    themes_left = [t for t in theme_sequence if t not in completed_themes and t != current_theme]
    last_question_text = question_history[-1] if question_history else None
    next_question_id = None if completed else f"q{total_question_count + 1}"
    question_history_tail = question_history[-5:] if question_history else []

    return {
        "survey_id": survey_id,
        "completed": completed,
        "current_theme": current_theme,
        "completed_themes": completed_themes,
        "themes_left": themes_left,
        "theme_sequence": theme_sequence,
        "total_question_count": total_question_count,
        "next_question_id": next_question_id,
        "last_question_text": last_question_text,
        "question_history_tail": question_history_tail,
        "theme_question_counts": theme_question_counts,
        # Context for UI
        "role": survey_ctx.get("role"),
        "business_area": survey_ctx.get("business_area"),
        "goals": survey_ctx.get("goals", []),
    }


@router.get("/company/{company_token}/report")
def company_report(company_token: str):
    report = build_company_report(company_token)
    if report["respondents"] == 0:
        raise HTTPException(status_code=404, detail="No respondents for this company yet.")
    return report
