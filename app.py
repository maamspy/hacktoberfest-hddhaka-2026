import os
import re

import requests
import streamlit as st

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

MODEL_GROUPS = {
    "Gemma 4 (open-weight, served by Google)": [
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
    ],
    "Gemini (Google hosted)": [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
    ],
}

DEFAULT_MODEL = "gemma-4-26b-a4b-it"

FALLBACKS = {
    "gemma-4-31b-it": ["gemma-4-26b-a4b-it", "gemini-2.5-flash"],
    "gemma-4-26b-a4b-it": ["gemma-4-31b-it", "gemini-2.5-flash"],
    "gemini-2.5-pro": ["gemini-2.5-flash", "gemini-2.0-flash"],
    "gemini-2.5-flash": ["gemini-2.0-flash", "gemma-4-26b-a4b-it"],
    "gemini-2.0-flash": ["gemini-2.5-flash", "gemma-4-26b-a4b-it"],
}

REQUEST_TIMEOUT = 120

API_KEY_VAR = "GOOGLE_AI_STUDIO_API_KEY"
DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


class ChatError(Exception):
    pass


class UnavailableError(ChatError):
    pass


def load_dotenv(path=DOTENV_PATH):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except FileNotFoundError:
        return {}

    loaded = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.split(" #", 1)[0].strip().strip("\"'")
        if name:
            loaded[name] = value
            os.environ.setdefault(name, value)
    return loaded


def get_api_key(path=DOTENV_PATH):
    key = os.environ.get(API_KEY_VAR, "").strip()
    if key:
        return key

    load_dotenv(path)
    key = os.environ.get(API_KEY_VAR, "").strip()
    if key:
        return key

    raise ChatError(
        f"Set `{API_KEY_VAR}` in a `.env` file at the project root. "
        "Copy `.env.example` to `.env` and paste in a free key from "
        "https://aistudio.google.com/apikey"
    )


def strip_thoughts(text):
    if not text:
        return ""
    text = re.sub(r"<\|channel>thought\b.*?<channel\|>", "", text, flags=re.DOTALL)
    text = re.sub(r"<\|channel>thought\s*<channel\|>", "", text)
    return text.strip()


def ask(messages, model, api_key=None):
    if api_key is None:
        api_key = get_api_key()
    if not api_key:
        raise ChatError(
            f"Set `{API_KEY_VAR}` in a `.env` file at the project root. Get a "
            "free key at https://aistudio.google.com/apikey"
        )

    url = f"{API_BASE}/{model}:generateContent?key={api_key}"

    contents = [
        {
            "role": "model" if msg["role"] == "assistant" else "user",
            "parts": [{"text": msg["content"]}],
        }
        for msg in messages
    ]

    try:
        response = requests.post(
            url,
            json={"contents": contents},
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise ChatError("Could not reach generativelanguage.googleapis.com.")
    except requests.exceptions.Timeout:
        raise ChatError(f"Google AI Studio did not respond within {REQUEST_TIMEOUT}s.")

    try:
        result = response.json()
    except ValueError:
        raise ChatError(
            f"Non-JSON response from the API (HTTP {response.status_code})."
        )

    if response.status_code != 200:
        detail = (
            result.get("error", {}).get("message", result)
            if isinstance(result, dict)
            else result
        )
        detail_text = str(detail).lower()

        if (
            response.status_code in (404, 429, 500, 503)
            or "quota" in detail_text
            or "overloaded" in detail_text
        ):
            raise UnavailableError(f"`{model}` is unavailable: {detail}")

        raise ChatError(
            f"Request failed (HTTP {response.status_code}).\n\n**Details**: {detail}"
        )

    candidates = result.get("candidates", [])
    if not candidates:
        feedback = result.get("promptFeedback", {}).get("blockReason")
        if feedback:
            raise ChatError(f"This prompt was blocked by the API ({feedback}).")
        raise ChatError("The API returned no candidates.")

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(
        p.get("text", "") for p in parts if not p.get("thought", False)
    ).strip()
    text = strip_thoughts(text)

    if not text:
        raise ChatError("The API returned an empty response (reasoning parts only).")
    return text


def ask_with_fallback(messages, model, api_key=None):
    chain = [model] + [m for m in FALLBACKS.get(model, []) if m != model]
    problems = []

    for candidate in chain:
        try:
            return ask(messages, candidate, api_key), candidate
        except UnavailableError as exc:
            problems.append(str(exc))

    raise ChatError(problems[-1] + f"\n\nTried: {', '.join(chain)}.")


st.set_page_config(page_title="Gemma 4 Chat", layout="centered")

st.markdown(
    """
    <style>
    .stApp { max-width: 800px; margin: 0 auto; }
    .main .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    h1 {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-weight: 700;
        text-align: center;
        margin-bottom: 1.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Gemma 4 Chat")

st.sidebar.title("Configuration")

try:
    api_key = get_api_key()
except ChatError as exc:
    api_key = ""
    st.sidebar.error(str(exc))
    st.sidebar.markdown(
        "1. Copy `.env.example` to `.env`\n"
        "2. Put your key in it\n"
        "3. Restart the app\n\n"
        "[Get a free key](https://aistudio.google.com/apikey)"
    )

family = st.sidebar.selectbox("Model family", list(MODEL_GROUPS.keys()))

models = MODEL_GROUPS[family]
model = st.sidebar.selectbox(
    "Model",
    models,
    index=models.index(DEFAULT_MODEL) if DEFAULT_MODEL in models else 0,
    key=f"model_{family}",
)

st.sidebar.caption(
    "Gemma 4 is open-weight (Apache 2.0) but hosted by Google, so it uses the "
    "same key and the same free-tier quota as the Gemini models."
)

if st.sidebar.button("Clear conversation", use_container_width=True):
    st.session_state.messages = []
    st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask your question..."):
    st.chat_message("user").markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        placeholder = st.empty()
        placeholder.markdown("Thinking...")

        try:
            answer, used = ask_with_fallback(
                st.session_state.messages, model, api_key or None
            )
            if used != model:
                st.info(f"`{model}` was unavailable, so `{used}` answered instead.")
        except ChatError as exc:
            answer = f"**Error:** {exc}"
        except Exception as exc:
            answer = f"**Unexpected error:** {exc}"

        placeholder.markdown(answer)
        st.session_state.messages.append({"role": "assistant", "content": answer})
