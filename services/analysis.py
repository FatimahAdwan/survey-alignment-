import os
import json
from collections import Counter, defaultdict
from typing import List, Dict
from dotenv import load_dotenv
from db import supabase

# OpenAI setup
from openai import OpenAI
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# To control cost/latency: cap how many answers will LLM-score per theme
MAX_RESPONSES_PER_THEME = 60   

# LLM helpers 

def llm_label_sentiment(text: str) -> str:
    """Classify survey answer as positive / neutral / negative using OpenAI."""
    try:
        prompt = f"""
Classify the sentiment of the following employee survey answer as exactly one label:
- positive
- neutral
- negative

Answer with ONLY one of those words. No explanations.

Answer:
{text.strip()}
"""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise sentiment classifier that outputs only one word."},
                {"role": "user", "content": prompt}
            ],
            temperature=0
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        if label not in {"positive", "neutral", "negative"}:
            return "neutral"
        return label
    except Exception:
        return "neutral"


def llm_theme_summary(company: str, theme: str, role_counts: Dict[str, int], dept_counts: Dict[str, int], answers: List[str]) -> Dict:
    """Summarize trending issues + actionable recommendations for a theme."""
    try:
        sample = answers[:200]
        role_line = ", ".join([f"{k}({v})" for k, v in role_counts.items()]) or "N/A"
        dept_line = ", ".join([f"{k}({v})" for k, v in dept_counts.items()]) or "N/A"

        prompt_context = f"""
You are analyzing anonymized employee survey answers for a single company.

Company: {company}
Theme: {theme}
Role mix: {role_line}
Department mix: {dept_line}

Answers (summarize patterns; do NOT quote exact sentences):
- """ + "\n- ".join([a.strip() for a in sample if a and a.strip()])

        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert HR insights analyst. Be concise, actionable, and anonymous."},
                {"role": "user", "content": prompt_context},
                {"role": "user", "content": f"""
Return JSON ONLY in this exact shape:

{{
  "trending_issues": ["issue 1", "issue 2", "issue 3"],
  "recommendations": ["rec 1", "rec 2", "rec 3"]
}}

Rules:
- Each item must be short (≤18 words), concrete, and theme-specific.
- Always include at least 2 issues and 2 recommendations.
- No names, no emails, no quotes.
"""}
            ],
            temperature=0.2
        )

        txt = resp.choices[0].message.content or "{}"
        data = json.loads(txt)
        trending = data.get("trending_issues", []) or []
        recs = data.get("recommendations", []) or []

    except Exception:
        trending, recs = [], []

    # fallback enforcement
    if len(trending) < 2:
        trending = [
            f"Mixed clarity around {theme.lower()} across roles",
            f"Inconsistent practices regarding {theme.lower()} between departments"
        ][:3]

    if len(recs) < 2:
        recs = [
            f"Define clear ownership and KPIs for {theme.lower()}",
            f"Create a monthly cadence to review {theme.lower()} progress"
        ][:3]

    return {"trending_issues": trending[:5], "recommendations": recs[:5]}


def llm_overall_summary(company: str, all_issues: List[str], all_recs: List[str]) -> Dict:
    """Generate an overall summary across all themes."""
    try:
        prompt_context = f"""
Company: {company}

Trending issues collected across themes:
- {"; ".join(all_issues)}

Recommendations collected across themes:
- {"; ".join(all_recs)}

Task: Summarize recurring cross-theme issues and give 2–3 overall company-level recommendations.
"""
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an expert HR insights analyst. Respond in concise JSON only."},
                {"role": "user", "content": prompt_context},
                {"role": "user", "content": """
Return JSON ONLY in this exact shape:

{
  "recurring_issues": ["issue 1", "issue 2"],
  "recommendations": ["rec 1", "rec 2"]
}
"""}
            ],
            temperature=0.2
        )
        txt = resp.choices[0].message.content or "{}"
        data = json.loads(txt)
        return {
            "recurring_issues": data.get("recurring_issues", [])[:3],
            "recommendations": data.get("recommendations", [])[:3],
        }
    except Exception:
        return {
            "recurring_issues": ["Cross-theme clarity gaps", "Reporting cadence limits timely actions"],
            "recommendations": ["Tailor goal communication", "Adopt more frequent reporting cadence"]
        }


# Helpers 

def _to_percentages(counts: Counter) -> Dict[str, str]:
    total = sum(counts.values()) or 1
    pct = {k: f"{round((v/total)*100)}%" for k, v in counts.items()}
    for key in ("positive", "neutral", "negative"):
        pct.setdefault(key, "0%")
    return pct

def _make_theme_ranking(theme_reports: Dict[str, Dict]) -> List[Dict]:
    """
    Build a ranked list of themes by 'positivity score' = positive% - negative%.
    Expects theme_reports[theme]["sentiment"] = {"positive": "62%", "neutral": "...", "negative": "..."}.
    """
    ranking = []
    for theme, data in theme_reports.items():
        sent = data.get("sentiment", {})
        try:
            pos = int(str(sent.get("positive", "0%")).replace("%", "") or 0)
            neg = int(str(sent.get("negative", "0%")).replace("%", "") or 0)
        except Exception:
            pos, neg = 0, 0

        score = pos - neg  # range roughly -100..+100

        # lightweight, auto summary
        if score >= 60:
            summary = f"Strongest theme; highly positive sentiment."
        elif score >= 30:
            summary = f"Generally positive with a few concerns."
        elif score >= 10:
            summary = f"Mixed but leaning positive."
        elif score > -10:
            summary = f"Mixed/neutral sentiment."
        else:
            summary = f"Weak area; address concerns urgently."

        ranking.append({
            "theme": theme,
            "positivity_score": score,
            "summary": summary
        })

    # Sort: highest score first
    ranking.sort(key=lambda x: x["positivity_score"], reverse=True)

    # Add rank numbers
    for idx, item in enumerate(ranking, start=1):
        item["rank"] = idx

    return ranking


# Main builder

def build_company_report(company_token: str) -> dict:
    """Company-level, anonymized, theme-based report."""
    sv = (supabase.table("surveys")
          .select("id, role, business_area")
          .eq("company_token", company_token)
          .execute().data) or []
    if not sv:
        return {"company": company_token, "respondents": 0, "themes": {}}

    survey_ids = [row["id"] for row in sv]
    role_counts = Counter([row.get("role") for row in sv if row.get("role")])
    dept_counts = Counter([row.get("business_area") for row in sv if row.get("business_area")])
    respondent_count = len(set(survey_ids))
    small_sample = respondent_count < 5

    resp = (supabase.table("responses")
            .select("survey_id, theme, question_text, answer")
            .in_("survey_id", survey_ids)
            .execute().data) or []

    by_theme = defaultdict(list)
    for r in resp:
        theme = r.get("theme") or "Unknown"
        text = " ".join([
            str(r.get("answer") or ""),
            " | Q: ",
            str(r.get("question_text") or "")
        ]).strip()
        if text:
            by_theme[theme].append(text)

    theme_reports = {}
    overall_counts = Counter()
    all_issues, all_recs = [], []

    for theme, texts in by_theme.items():
        sample_for_sentiment = texts[:MAX_RESPONSES_PER_THEME]

        counts = Counter()
        for t in sample_for_sentiment:
            label = llm_label_sentiment(t)
            counts[label] += 1
            overall_counts[label] += 1

        # include raw counts alongside percentages 
        counts_dict = {
            "positive": counts.get("positive", 0),
            "neutral":  counts.get("neutral", 0),
            "negative": counts.get("negative", 0),
        }  # NEW
        sentiment_pct = _to_percentages(counts)

        summary = llm_theme_summary(
            company=company_token,
            theme=theme,
            role_counts=dict(role_counts),
            dept_counts=dict(dept_counts),
            answers=texts
        )

        theme_reports[theme] = {
            "responses": len(texts),
            "sentiment_counts": counts_dict,   # NEW
            "sentiment": sentiment_pct,
            "trending_issues": summary.get("trending_issues", []),
            "recommendations": summary.get("recommendations", []),
        }

        all_issues.extend(summary.get("trending_issues", []))
        all_recs.extend(summary.get("recommendations", []))

        if small_sample:
            theme_reports[theme]["sentiment_percent_note"] = (
                f"Based on {respondent_count} respondents; interpret with caution."
            )

    overall = llm_overall_summary(company_token, all_issues, all_recs)
    overall_sentiment = _to_percentages(overall_counts)

    # --- NEW: overall counts too ---
    overall_counts_dict = {
        "positive": overall_counts.get("positive", 0),
        "neutral":  overall_counts.get("neutral", 0),
        "negative": overall_counts.get("negative", 0),
    }  # NEW

    theme_ranking = _make_theme_ranking(theme_reports)

    report = {
        "company": company_token,
        "respondents": respondent_count,
        "roles": dict(role_counts),
        "business_areas": dict(dept_counts),
        "themes": theme_reports,
        "overall_sentiment_counts": overall_counts_dict,  
        "overall_sentiment": overall_sentiment,
        "overall_summary": overall,
        "theme_ranking": theme_ranking,
        "notes": ["Anonymized; no names/emails/IDs included."]
    }
    if small_sample:
        report["notes"].append(f"Based on {respondent_count} respondents; interpret with caution.")

    return report
