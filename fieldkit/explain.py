"""Local Gemma, used only to explain a decision a human is looking at.

Laya decides. Gemma explains. Prose never feeds back into a label,
because a generator cannot produce calibrated probabilities and a
classifier cannot produce a sentence. Keeping them in separate roles is
what makes the confidence numbers trustworthy.

The generator itself comes from `fieldkit.llm`, which prefers a local
model and falls back to Google AI Studio if a key is configured. Either
way it only ever produces prose for a human to read.
"""

from __future__ import annotations

from fieldkit import llm

PROMPT = """You are explaining a classification to an engineer.

The classifier picked: {answer} (confidence {confidence:.0%})
Reason it escalated: {escalated}

Full distribution:
{distribution}

Original text:
{subject}

In at most three sentences, say why that choice is reasonable and what a
human should check. Do not restate the label as if it were certain.
"""

ASK_PROMPT = """{context}A field engineer is reviewing a decision that was escalated to
them, and they ask:

{question}

Answer in at most four sentences. Be concrete about what they should check
before signing off. Do not restate the classification as settled.
"""

FALLBACK = (
    "No text generator is available, so the question cannot be answered.\n\n"
    "Build llama.cpp and put `llama-cli` on PATH, download a local Gemma "
    "with transformers, or set GOOGLE_AI_STUDIO_API_KEY in .env."
)


def _format_distribution(distribution: dict | None) -> str:
    if not distribution:
        return "  (not reported)"
    return "\n".join(f"  {key}: {float(value):.0%}" for key, value in distribution.items())


def build_prompt(verdict: dict, subject: str) -> str:
    return PROMPT.format(
        answer=verdict.get("proposed") or "unknown",
        confidence=float(verdict.get("confidence") or 0.0),
        escalated="yes" if float(verdict.get("confidence") or 0.0) < 0.8 else "no",
        distribution=_format_distribution(verdict.get("distribution")),
        subject=subject,
    )


def build_ask_prompt(question: str, verdict: dict | None = None, subject: str = "") -> str:
    if verdict:
        context = (
            f"Laya classified this as `{verdict.get('proposed')}` at confidence "
            f"{float(verdict.get('confidence') or 0.0):.0%}.\n\n"
            f"Reason it escalated: {verdict.get('reason')}\n\n"
            f"{_format_distribution(verdict.get('distribution'))}\n\n"
        )
    else:
        context = ""

    if subject:
        context += f"The text under review:\n{subject}\n\n"

    return ASK_PROMPT.format(context=context, question=question.strip())


def generate(prompt: str, max_tokens: int = 4096, preference: str = "auto") -> str:
    """Answer with whichever generator is available."""
    backend = llm.resolve(preference)
    if not backend.available():
        return FALLBACK
    return backend.generate(prompt, max_tokens)


def explain(verdict: dict, subject: str, max_tokens: int = 4096,
            preference: str = "auto") -> str:
    """Explain why Laya produced the answer it did."""
    return generate(build_prompt(verdict, subject), max_tokens, preference)


def ask(question: str, verdict: dict | None = None, subject: str = "",
        max_tokens: int = 4096, preference: str = "auto") -> str:
    """Answer a reviewer's follow-up question about an escalated item.

    The answer is prose for a human. It is never parsed back into a label,
    which is the whole reason this lives beside the decision path instead
    of inside it.
    """
    return generate(build_ask_prompt(question, verdict, subject), max_tokens, preference)
