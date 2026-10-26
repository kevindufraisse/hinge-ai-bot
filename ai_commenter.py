# ai_commenter.py
import re

from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

MODEL = "gpt-5.2"
MAX_LEN = 80
N_CANDIDATES = 4

FALLBACK = "do you want to know a secret?"


def _soft_trim(s: str, limit: int = MAX_LEN) -> str:
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut if cut else s[:limit].rstrip()


def _clean_line(line: str) -> str:
    """Enlève un éventuel marqueur de liste ('1.', '-', '•') sans casser les
    messages qui commencent légitimement par un chiffre."""
    line = line.strip()
    line = re.sub(r"^(?:\d+[.)]\s*|[-•*]\s+)", "", line)
    return line.strip().strip('"').strip()


SYSTEM_PROMPT = f"""You write the first Hinge message that gets a reply.

You only see ONE prompt + its answer. No name, no photos, nothing else.
Never invent details — work with exactly what's given.

GOAL: a reply. It must sound written for THIS person, not for anyone.

RULES
- One line, under {MAX_LEN} chars, no quotes (pasted via clipboard).
- Reference something specific: a detail, a number, a named thing, an opinion.
- One hook only. End with a question that's easy to answer — unless the line already invites a reply.
- Mirror their tone (casual / witty / deadpan). Don't copy it.

HARD NOs
- Complimenting looks or the prompt itself ("great answer", "love this").
- Asking something they already answered in the snippet.
- A line you could send to 100 people unchanged.
- Emojis as personality, intensity, or being over-familiar.

IF THE ANSWER IS VAGUE OR EMPTY
- Make a light, self-aware comment about the situation — playful, not mean.
"""


def get_ai_comment(prompt: str) -> str:
    if not prompt:
        return ""

    try:
        response = client.responses.create(
            model=MODEL,
            instructions=SYSTEM_PROMPT,
            input=(
                "You only have this ONE snippet from their profile "
                "(no name, no photos, nothing else). Never invent details.\n\n"
                f"{prompt}\n\n"
                f"Write {N_CANDIDATES} different opening messages, "
                "best first, one per line. Plain text, no numbering or bullets."
            ),
            max_output_tokens=200,
            temperature=0.7,
        )

        raw = response.output_text.strip()

        candidates = []
        for line in raw.splitlines():
            line = _clean_line(line)
            if line:
                candidates.append(_soft_trim(line, MAX_LEN))

        if not candidates:
            return FALLBACK

        # Le modèle a classé du meilleur au moins bon → on prend le premier.
        return candidates[0]

    except Exception as e:
        print(f"[ai_commenter] OpenAI error: {e}")
        return FALLBACK
