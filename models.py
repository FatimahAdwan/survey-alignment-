from pydantic import BaseModel
from typing import Union, List, Any

class StartSurveyRequest(BaseModel):
    full_name: str
    email: str
    company_name: str
    role: str
    business_area: str
    goals: List[str]

class QuestionResponse(BaseModel):
    survey_id: Union[str, int]
    question_id: str
    text: str
    type: str
    options: List[str]

class AnswerRequest(BaseModel):
    survey_id: str
    question_id: str
    question_text: str
    answer: Any  # <-- Accepts str, int, list, bool, etc.

class FollowUpResponse(BaseModel):
    question_id: str
    text: str
    type: str
    options: List[str] = []

