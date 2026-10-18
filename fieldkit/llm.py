"""Text generation backends for the follow-up agent.

The decision path is fully local and always will be -- Laya runs on your
machine and nothing it decides leaves it. Only the *explanation* needs a
generator, and that has three possible homes. This module picks one.

Priority is local first, because a local model keeps customer text on the
machine:

1. `llama-cli` on PATH -- llama.cpp. Fastest to start once built.
2. A local `transformers` model, e.g. `google/gemma-4-E4B-it`. Slower on
   CPU, but no external service.
3. Google AI Studio, if an API key is configured. Uses the network.

If none is available the caller gets a clear message rather than a
silent empty answer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from fieldkit.dotenv import load_dotenv

DEFAULT_TRANSFORMERS_MODEL = "google/gemma-4-E4B-it"
DEFAULT_LLAMA_MODEL = "ggml-org/gemma-4-E2B-it-GGUF"
GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GOOGLE_API_KEY_VAR = "GOOGLE_AI_STUDIO_API_KEY"


def llama_binary() -> str | None:
    return shutil.which("llama-cli")


def google_api_key() -> str:
    load_dotenv()
    return (os.environ.get(GOOGLE_API_KEY_VAR) or "").strip()


def google_models() -> list[str]:
    """Hosted models that can answer, best first."""
    return [
        "gemma-4-26b-a4b-it",
        "gemma-4-31b-it",
        "gemini-2.5-flash",
    ]


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


class Backend:
    name = "none"
    detail = ""

    def available(self) -> bool:
        return False

    def generate(self, prompt: str, max_tokens: int = 200) -> str:
        raise NotImplementedError


class LlamaCppBackend(Backend):
    name = "llama.cpp"

    def __init__(self, model: str = DEFAULT_LLAMA_MODEL):
        self.model = model
        self.binary = llama_binary()

    def available(self) -> bool:
        return bool(self.binary)

    def detail(self) -> str:  # type: ignore[override]
        return f"{self.binary} ({self.model})"

    def generate(self, prompt: str, max_tokens: int = 200) -> str:
        command = [
            self.binary, "-hf", self.model,
            "-p", prompt,
            "-n", str(max_tokens),
            "--temp", "0.2",
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300, check=False
            )
        except subprocess.TimeoutExpired:
            return "Gemma did not finish within 300s."
        except OSError as exc:
            return f"Could not start llama-cli: {exc}"

        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
            return f"Gemma failed (exit {result.returncode}): {tail[0]}"

        return result.stdout.strip() or "Gemma returned nothing."


class TransformersBackend(Backend):
    name = "transformers"

    def __init__(self, model: str = DEFAULT_TRANSFORMERS_MODEL):
        self.model = model
        self._pipe = None

    def available(self) -> bool:
        """True only when the weights are already on disk.

        This backend is deliberately *not* offered as available when the
        model would have to be downloaded. A several-gigabyte fetch
        triggered by someone clicking a button is a bad surprise, and it
        would happen on a customer laptop on a conference wifi. Use
        `fieldkit llm pull` to fetch it deliberately.
        """
        if not self.model:
            return False
        try:
            import transformers  # noqa: F401
        except Exception:
            return False
        return self.cached()

    def cached(self) -> bool:
        """Are the weights already in the Hugging Face cache?"""
        try:
            from huggingface_hub import try_to_load_from_cache
        except Exception:
            return False

        for filename in ("config.json", "model.safetensors", "tokenizer_config.json"):
            hit = try_to_load_from_cache(self.model, filename)
            if isinstance(hit, str) and hit:
                return True
        return False

    def detail(self) -> str:  # type: ignore[override]
        state = "cached" if self.cached() else "not downloaded"
        return f"{self.model} ({state})"

    def _load(self):
        if self._pipe is not None:
            return self._pipe

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(self.model)
        model = AutoModelForCausalLM.from_pretrained(
            self.model, dtype=torch.float32, device_map="cpu"
        )
        model.eval()
        self._pipe = (tokenizer, model)
        return self._pipe

    def generate(self, prompt: str, max_tokens: int = 200) -> str:
        try:
            import torch

            tokenizer, model = self._load()
            messages = [{"role": "user", "content": prompt}]
            try:
                text = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            except Exception:
                text = prompt

            inputs = tokenizer(text, return_tensors="pt")
            with torch.no_grad():
                output = model.generate(
                    **inputs, max_new_tokens=max_tokens, do_sample=False
                )
            decoded = tokenizer.decode(
                output[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
            )
            return decoded.strip() or "Gemma returned nothing."
        except Exception as exc:
            return f"Local Gemma failed: {' '.join(str(exc).split())[:200]}"


class GoogleAIStudioBackend(Backend):
    name = "google-ai-studio"

    def __init__(self, model: str | None = None):
        self.model = model or (google_models() or ["gemini-2.5-flash"])[0]
        self.key = google_api_key()

    def available(self) -> bool:
        return bool(self.key)

    def detail(self) -> str:  # type: ignore[override]
        return f"{self.model} (key from {GOOGLE_API_KEY_VAR})"

    def generate(self, prompt: str, max_tokens: int = 4096) -> str:
        if not self.key:
            return "No Google AI Studio key configured."

        import requests

        url = f"{GOOGLE_API_BASE}/{self.model}:generateContent?key={self.key}"

        def call(budget: int):
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": budget,
                    "temperature": 0.2,
                },
            }
            return requests.post(
                url, json=body, headers={"Content-Type": "application/json"}, timeout=300
            )

        try:
            response = call(max_tokens)
        except requests.exceptions.RequestException as exc:
            return f"Could not reach Google AI Studio: {exc}"

        # The API intermittently returns 500 on a request that succeeds on
        # a retry. A reviewer waiting on an answer should not have to press
        # the button again.
        attempts = 0
        while response.status_code >= 500 and attempts < 3:
            attempts += 1
            time.sleep(1.5 * attempts)
            try:
                response = call(max_tokens)
            except requests.exceptions.RequestException as exc:
                return f"Could not reach Google AI Studio: {exc}"

        if response.status_code != 200:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                detail = response.text
            return f"Google AI Studio returned HTTP {response.status_code}: {detail}"

        try:
            payload = response.json()
        except ValueError:
            return "Google AI Studio returned a non-JSON response."

        candidates = payload.get("candidates", [])
        if not candidates:
            return "Google AI Studio returned no candidates."

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought", False)).strip()

        if text:
            return text

        # The model spent the whole output budget thinking and never
        # reached an answer. gemma-4 has no thinkingBudget control
        # ("Thinking budget is not supported for this model"), so the only
        # lever is a bigger budget.
        #
        # One generous retry, not a chain. An escalating chain sounds
        # clever and costs minutes: a "say hello" prompt once burned
        # 4096 thinking tokens, then 8192, then 24576, for a 222 second
        # round trip. Two attempts, one of them big, is the trade.
        thoughts = payload.get("usageMetadata", {}).get("thoughtsTokenCount", 0)
        if thoughts:
            try:
                response = call(16384)
                if response.status_code == 200:
                    payload = response.json()
                    parts = payload["candidates"][0]["content"]["parts"]
                    text = "".join(
                        p.get("text", "") for p in parts if not p.get("thought", False)
                    ).strip()
                    if text:
                        return text
            except (requests.exceptions.RequestException, KeyError, ValueError, IndexError):
                pass
            return (
                "The model used the whole output budget thinking and returned "
                f"no answer ({thoughts} thinking tokens). Try again, or ask a "
                "narrower question."
            )

        return "Google AI Studio returned an empty response."


class NullBackend(Backend):
    name = "none"

    def detail(self) -> str:  # type: ignore[override]
        return "no generator configured"

    def generate(self, prompt: str, max_tokens: int = 200) -> str:
        return (
            "No text generator is configured, so there is nothing to answer "
            "the question.\n\n"
            "Pick one:\n"
            "  - build llama.cpp and put `llama-cli` on PATH\n"
            f"  - download a local Gemma with transformers "
            f"(default: {DEFAULT_TRANSFORMERS_MODEL})\n"
            f"  - set {GOOGLE_API_KEY_VAR} in .env to use Google AI Studio"
        )


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def resolve(preference: str = "auto") -> Backend:
    """Pick a backend.

    `preference` is "auto", "local", "api", or a specific backend name.

    "auto" order: llama.cpp, then a local transformers model *if its
    weights are already cached*, then Google AI Studio. The API is
    preferred over an uncached local model because a several-gigabyte
    download triggered by a button click is not a good default.
    """
    preference = (preference or "auto").strip().lower()

    backends: list[Backend] = [
        LlamaCppBackend(),
        TransformersBackend(),
        GoogleAIStudioBackend(),
    ]

    if preference in ("", "auto"):
        for backend in backends:
            if backend.available():
                return backend
        return NullBackend()

    if preference == "local":
        for backend in backends[:2]:
            if backend.available():
                return backend
        return NullBackend()

    if preference == "api":
        api = GoogleAIStudioBackend()
        return api if api.available() else NullBackend()

    for backend in backends:
        if backend.name == preference:
            return backend if backend.available() else NullBackend()

    return NullBackend()


def describe() -> list[dict]:
    """A report of every backend and whether it is usable."""
    report = []
    for backend in (LlamaCppBackend(), TransformersBackend(), GoogleAIStudioBackend()):
        report.append({
            "name": backend.name,
            "available": backend.available(),
            "detail": backend.detail(),
        })
    return report


def pull(model: str = DEFAULT_TRANSFORMERS_MODEL) -> str:
    """Deliberately download a local model for the transformers backend.

    Separate from `generate` on purpose: a large download should be
    something you asked for, not something a button press triggers.
    """
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        return f"huggingface_hub is unavailable: {exc}"

    try:
        snapshot_download(model)
    except Exception as exc:
        return f"Download failed: {' '.join(str(exc).split())[:200]}"
    return f"Downloaded {model}."
