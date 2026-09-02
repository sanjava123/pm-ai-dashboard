# src/llm_client.py
import os
import json
import logging
from typing import Dict, Any, List
from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Initialize Client (Reads GROQ_API_KEY from env automatically)
try:
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception as e:
    logger.error(f"Groq Client Init Failed: {e}")
    client = None

MODEL = "llama3-70b-8192" # Best reasoning on Free Tier. Use "llama3-8b-8192" for speed.

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(2))
def call_groq_json(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> Dict:
    """Calls Groq with JSON response format enforced."""
    if not client:
        return {"error": "Groq Client not initialized. Check API Key."}
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=MODEL,
            temperature=temperature,
            response_format={"type": "json_object"}, # ENFORCES VALID JSON
            max_tokens=4096
        )
        content = chat_completion.choices[0].message.content
        return json.loads(content)
    except json.JSONDecodeError:
        logger.error(f"Groq returned non-JSON: {content}")
        return {"error": "LLM returned invalid JSON", "raw": content}
    except Exception as e:
        logger.error(f"Groq API Error: {e}")
        return {"error": str(e)}

def generate_sprint_summary(kpis: Dict, team_name: str, sprint_name: str) -> Dict:
    """Asks LLM to act as Senior PM writing a Stakeholder Update."""
    system = f"""You are a Senior Technical Program Manager. 
    Write a concise, professional Sprint Summary for {team_name} ({sprint_name}).
    Audience: Engineering Leadership & Stakeholders.
    Tone: Objective, Data-Driven, Action-Oriented. No fluff.
    Output MUST be valid JSON with keys: "headline", "health_score" (1-10), "key_achievements", "risks_blockers", "recommendations"."""
    
    user = f"""SPRINT METRICS:
Predictability (Say/Do): {kpis.get('predictability', 0)}%
Committed: {kpis.get('committed_pts', 0)} pts | Done: {kpis.get('completed_pts', 0)} pts
Cycle Time (P85): {kpis.get('cycle_time_p85', 0)} days
Throughput: {kpis.get('throughput', 0)} tickets
Bug Ratio: {kpis.get('bug_ratio', 0)*100:.0f}%
Aging WIP (>5d): {kpis.get('aging_wip_count', 0)} tickets
Carryover: {kpis.get('carryover_count', 0)} tickets

AGING WIP DETAILS:
{json.dumps(kpis.get('aging_wip_details', []), indent=2)}

CARRYOVER DETAILS:
{json.dumps(kpis.get('carryover_details', []), indent=2)}
"""
    return call_groq_json(system, user, temperature=0.2)

def generate_retro_prep(df: pd.DataFrame, sprint: str) -> Dict:
    """Analyzes ticket titles/comments to suggest Retro topics."""
    sprint_df = df[df["Sprint"] == sprint]
    # Sample tickets to fit context window
    sample = sprint_df.sample(min(20, len(sprint_df)))["Summary"].tolist()
    
    system = """You are an Agile Coach. Analyze ticket titles to suggest 3 Retrospective Topics.
    Output JSON: {"topics": [{"title": "...", "data_evidence": "...", "suggested_activity": "Start/Stop/Continue / Sailboat / 4Ls"}]}"""
    
    user = f"Ticket Titles for {sprint}:\n" + "\n".join([f"- {t}" for t in sample])
    return call_groq_json(system, user, temperature=0.4)
