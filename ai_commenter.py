# ai_commenter.py
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

MODEL = "gpt-5.2"
MAX_LEN = 80
N_CANDIDATES = 4

def _soft_trim(s: str, limit: int = MAX_LEN) -> str:
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut if cut else s[:limit].rstrip()

def _score_candidate(text: str) -> float:
    score = 0.0
    if "?" in text:
        score += 2.0
    if len(text) >= 40:
        score += 1.0
    if len(text) <= MAX_LEN:
        score += 1.0

    generic = ["haha", "lol", "so true", "interesting", "wow"]
    if any(w in text.lower() for w in generic):
        score -= 1.5

    return score

SYSTEM_PROMPT = f"""You write irresistible Hinge comments that spark conversations.

THE GOAL: Make them think "this person actually read my profile AND is interesting"

CONSTRAINTS:
- Max {MAX_LEN} chars, single line, no quotes
- Sound like a text from a clever friend, not a copywriter

STRATEGY (pick ONE):
A) Hyper-specific callback → reference an oddly specific detail
B) Playful challenge → light disagreement that invites banter  
C) Self-aware confession → relatable vulnerability with humor
D) Pattern interrupt → say something unexpected that makes them pause

HARD NOs:
- Generic praise ("love this!", "you seem fun")
- Questions that feel like a job interview
- Anything that could be sent to 100 people unchanged
- Emojis as emotional crutches

VIBE CHECK: Read it back. Does it sound like a real person with a personality, or a bot trying to sound casual? If the latter, rewrite.
"""

def get_ai_comment(prompt: str) -> str:
    if not prompt:
        return ""

    try:
        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=f"Hinge prompt: {prompt}\nWrite {N_CANDIDATES} different comments, one per line.",
            max_output_tokens=200,
            temperature=0.7,
        )

        raw = response.output_text.strip()

        candidates = []
        for line in raw.splitlines():
            line = line.strip().lstrip("0123456789.-) ")
            if line:
                candidates.append(_soft_trim(line, MAX_LEN))

        if not candidates:
            return "do you want to know a secret?"

        valid_candidates = [c for c in candidates if c]
        if not valid_candidates:
            return "do you want to know a secret?"

        return max(valid_candidates, key=_score_candidate)

    except Exception as e:
        print(f"[ai_commenter] OpenAI error: {e}")
        return "do you want to know a secret?"