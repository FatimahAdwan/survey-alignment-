import re
import os
import json
import openai
from openai import OpenAI
from dotenv import load_dotenv
from db import mark_survey_complete
from db import supabase, save_response, init_survey_progress, update_survey_progress
from models import StartSurveyRequest, QuestionResponse, AnswerRequest, FollowUpResponse

# OpenAI setup
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# Helper to switch to the next theme
def get_next_theme(current_theme: str, theme_sequence: list, completed_themes: list):
    for theme in theme_sequence:
        if theme != current_theme and theme not in completed_themes:
            return theme
    return None  # all themes complete


# FIRST QUESTION GENERATION
def generate_first_question(data: StartSurveyRequest, survey_id) -> QuestionResponse:
    
    return QuestionResponse(
        survey_id=survey_id,
        question_id="q1",
        text="Do you know these goals?",
        type="multi-select",
        options=data.goals
    )

def generate_next_question(data: AnswerRequest) -> FollowUpResponse:
    # Fetch user context and survey progress
    user_result = supabase.table("surveys").select("*").eq("id", data.survey_id).single().execute()
    progress_result = supabase.table("survey_progress").select("*").eq("survey_id", data.survey_id).single().execute()
    user = user_result.data
    progress = progress_result.data

    if not user or not progress:
        return FollowUpResponse(
            question_id="error",
            text="Survey or progress data not found.",
            type="text"
        )

    current_theme = progress["current_theme"]
    def as_list(v):
        if v is None:
            return []
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v

    def as_dict(v):
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v
    theme_sequence = as_list(progress.get("theme_sequence", []))
    completed_themes = as_list(progress.get("completed_themes", []))
    question_history = as_list(progress.get("question_history", []))
    theme_question_counts = as_dict(progress.get("theme_question_counts", {}))

    total_question_count = int(progress.get("total_question_count", 0))
    next_total_count = total_question_count + 1
    next_question_id = f"q{next_total_count}"
    # make sure the survey doesn't go beyond the theme
    
    if set(completed_themes) == set(theme_sequence):
        mark_survey_complete(data.survey_id)
        return FollowUpResponse(
            question_id="done",
            text=" 🎉 thank you, you have completed the survey",
            type='text',
            options=[]
        )

    # Save the user's response
    save_response(
        survey_id=data.survey_id,
        question_id=data.question_id,
        question_text=data.question_text,
        answer=data.answer,
        theme = current_theme
    )

    # Update question count for current theme
    current_count = theme_question_counts.get(current_theme, 0) + 1
    theme_question_counts[current_theme] = current_count

    # If current theme has reached 5 questions, force theme switch
    force_theme_switch = current_count >= 5

    # Format last 5 asked questions TEXT as bullet to help improve anti_repeat
    recent_qs = question_history[-5:]
    question_list = "\n".join([f'- "{q}"' for q in recent_qs]) if recent_qs else "- (none yet)"


    # Build LLM prompt
    prompt = f"""
You are a structured workplace survey assistant helping gather actionable feedback on company goals.

The user’s role is: {user['role']}
They work in the: {user['business_area']} department.
Their top goals are: {", ".join(user['goals'])}

You're currently focusing on the theme: "{current_theme}"
The user's latest response was: "{data.answer}"
Questions already asked in this theme ( DO NOT ASK ANY OF THESE): {question_list}

Themes already completed: {", ".join(completed_themes)}
Themes left to cover: {", ".join([t for t in theme_sequence if t not in completed_themes and t != current_theme])}
Theme progress: {current_count} questions asked so far in this theme.
Global progress: {total_question_count} questions asked so far in the survey.
The NEXT question ID MUST be exactly "{next_question_id}". Do not use any other ID.



### STRICT RULES — DO NOT BREAK THESE:

1. **Ask 3-5 questions per theme**.  
    → If 3-5 questions are already asked in this theme, set `"switch_theme": true`.
    → where necessary if the need be, you can ask more than 3-5 questions
    → Avoid the "can you describe a situation where..." kind of questions
    → if the user response with they don't understand a goal or they only understand it partially, ask them why don't they know and what is stopping them from knowing
    → Add a rating/scoring questions at the end of some questions to help ascertain how strong the user's answers are.rating questions are very important
    → questions need to be similar across all similar roles and similar goals, this will help in later analyis to know what is going on across different departments etc

2. **You MUST follow the Theme-Specific Question Logic and Branching Guide below.**  
   → Ask ONLY questions that logically follow from the user's latest response, for example, under the theme clarity of goals, 
   if you asked the user if the user says they don't know the goal, ensure to ask them what prevent the from knowing the goal  
   → DO NOT skip any step in the branching.  very impotant not to skip!!!
   → DO NOT guess what’s next you must follow the flow strictly. this is also very important
   → The [G1], [G2], [G3] are used in place of the actual goals, so once the user provides the actual goals, replace the words [G1], [G2], [G3] with the actual goals the user provided

3. **DO NOT repeat phrasing or questions already asked in the same theme.**  
    If a question (or something very close) appears in `question_history`, DO NOT ask it again. Don not repeat!!, it's very imporatnt not to repeat questions

4. **ALWAYS increment question IDs in order** (e.g., "q1", "q2", "q3"...).  
    Never jump to q3 before asking q2.  
    Never reuse an earlier ID.

5. **Personalize questions using the user's role and department.**  
   Refer to their position and work context when possible.

6. **Vary question types**:  
   → Use a mix of `text` and `select`.  
   → Include both qualitative and quantitative questions where appropriate e.g "on a scale of 1-5 how well do you know these goals" or "on a scale of 1-5 how well can you support your answer". 

7. **NEVER ask unrelated or filler questions.**  
   You must stay on-topic, follow the branching logic, and stay within the theme.

---

### Theme-Specific Question Logic and Branching Guide:

#### Clarity of Goals:
1. Start with: “Do you know these goals: [G1], [G2], [G3]?” Options: Yes / Partial / No
2. If Yes:
   a. “Which goal drives your work most?” [G1]/[G2]/[G3]
   b. “How does that goal show up in your daily tasks?”
3. If Partial:
   a. “Which goal is clearest to you?” then “What makes it clear?” or “What’s unclear about the others?”
4. If No:
   b “What prevents you from knowing the goals?” Options: Never told / Too complex / Not communicated

#### Measurement of Progress:
1. Start: “Are your daily tasks measured against [G1], [G2], [G3]?” Options: Always / Sometimes / Never
2. If Always:
   → “Which goal is tracked most closely?”
3. If Sometimes:
   → “Which goal is sometimes tracked?” then “How are your tasks measured?” or “How often are metrics reviewed?”
4. If Never:
   → “What is measured instead?” Options: Outputs / Activity / Nothing clear

#### Visibility of Reports:
1. Start: “Do you see performance data for [G1], [G2], [G3]?” Options: Yes / Rarely / No
2. If Yes:
   → “Which goal’s data is most useful?” then “How do you use it?”
3. If No/Rarely:
   → “How often do you get updates?” Options: Weekly / Monthly / Never
   → “Does this delay affect your work on [top goal]?”

#### Frontline Impact:
1. Start: “Does your daily work feel connected to [G1], [G2], [G3]?” Options: Yes / Partial / No
2. If Yes:
   → “Which goal feels most connected?” then “What makes the connection strong?”
3. If Partial:
   → “Which goal feels least connected?” then “What would strengthen that connection?”
4. If No:
   → “What drives your work instead?” Options: Daily tasks / Boss directives / Survival

#### Priority Ranking:
- Final question: “Rank [G1], [G2], [G3] by importance.” (text or drag format)
- Then say: “Thanks, you’ve completed the survey!”

---

### Respond ONLY in this JSON format (nothing else):

{{
  "switch_theme": true or false,
  "next_theme": "theme_name" or null,
  "question_id": "{next_question_id}",
  "text": "Your next question",
  "type": "text" or "select",
  "options": ["option1", "option2", ...]  # empty if type is text
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an assistant that generates structured survey questions."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3
        )
        result = json.loads(response.choices[0].message.content)

        # Hard enforce our global continuous ID
        result["question_id"] = next_question_id

        # Cheap exact-duplicate guard (optional, keeps things clean)
        new_q_text = (result.get("text") or "").strip()
        if any((new_q_text.lower() == prev.lower()) for prev in question_history):
            # If exact duplicate, nudge the text a bit so it's not identical
            result["text"] = new_q_text + " — please be specific."


        # Track history
        question_history.append(result["text"])

        if result["switch_theme"] or force_theme_switch:
            completed_themes.append(current_theme)

        # Decide next theme
        if result.get("next_theme"):
            current_theme = result["next_theme"]
        elif result["switch_theme"] or force_theme_switch:
            current_theme = get_next_theme(current_theme, theme_sequence, completed_themes)

        # Update progress in Supabase
        supabase.table("survey_progress").update({
            "current_theme": current_theme,
            "question_history": json.dumps(question_history),
            "completed_themes": completed_themes,
            "theme_question_counts": theme_question_counts,
            "total_question_count": next_total_count
        }).eq("survey_id", data.survey_id).execute()

        # If all themes are completed after this update, mark as complete and finish
        if set(completed_themes) == set(theme_sequence):
            mark_survey_complete(data.survey_id)
            return FollowUpResponse(
                question_id="done",
                text="🎉 Thank you — you have completed the survey.",
                type="text",
                options=[]
            )


        return FollowUpResponse(
            question_id=result["question_id"],
            text=result["text"],
            type=result["type"],
            options=result.get("options", [])
        )

    except Exception as e:
        print("LLM Error:", e)
        return FollowUpResponse(
            question_id="error",
            text="There was an error generating the next question.",
            type="text"
        )