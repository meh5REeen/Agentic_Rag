import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
_GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")


def call_gemini(model_name, messages, temperature=0.0, max_tokens=600, timeout=60):
    if not _GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {_GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    for attempt in range(3):
        try:
            response = requests.post(_GROQ_BASE_URL, json=payload, headers=headers, timeout=timeout)
            if not response.ok:
                raise requests.exceptions.HTTPError(f"{response.status_code}: {response.text}")

            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except requests.exceptions.RequestException as exc:
            if attempt == 2:
                raise RuntimeError(f"Groq request failed: {exc}") from exc
            time.sleep(2)
