import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

from config.models import GROQ_MODEL_FALLBACK_CHAIN, build_model_chain

load_dotenv()

logger = logging.getLogger(__name__)

_GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
_GROQ_BASE_URL = os.getenv(
    "GROQ_BASE_URL", "https://api.groq.com/openai/v1/chat/completions"
)


class GroqAvailabilityError(Exception):
    """Groq model unavailable — safe to retry with the next model in the chain."""


class GroqNonRetryableError(Exception):
    """Client/input error — do not retry or fall back to another model."""


def _parse_error_payload(response: requests.Response) -> dict:
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return {"raw": response.text}


def _error_message(payload: dict) -> str:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or payload)
    return str(payload.get("raw") or payload)


def _is_availability_failure(status_code: int, payload: dict) -> bool:
    if status_code == 429:
        return True
    if 500 <= status_code <= 599:
        return True

    err = payload.get("error")
    if isinstance(err, dict):
        code = str(err.get("code", "")).lower()
        message = str(err.get("message", "")).lower()
        if code in {"model_not_found", "model_decommissioned"}:
            return True
        if "decommissioned" in message:
            return True
        if "rate_limit" in code or "rate limit" in message:
            return True
        if "overloaded" in message or "capacity" in message:
            return True
        if "unavailable" in message:
            return True
        if "model" in message and (
            "not found" in message
            or "does not exist" in message
            or "do not have access" in message
        ):
            return True

    if status_code == 404:
        return True

    return False


def _reasoning_kwargs(model: str, disable_reasoning: bool) -> dict:
    """Apply model-specific params to suppress visible reasoning on internal calls."""
    if not disable_reasoning:
        return {}
    model_lower = model.lower()
    if model_lower.startswith("qwen/") or "qwen" in model_lower:
        return {"reasoning_effort": "none"}
    if "gpt-oss" in model_lower:
        return {"reasoning_format": "hidden"}
    return {"reasoning_effort": "none"}


def _call_groq_once(
    model: str,
    messages: list,
    temperature: float,
    max_tokens: int,
    timeout: int,
    disable_reasoning: bool = False,
) -> str:
    if not _GROQ_API_KEY:
        raise GroqNonRetryableError("GROQ_API_KEY is not set")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_reasoning_kwargs(model, disable_reasoning),
    }
    headers = {
        "Authorization": f"Bearer {_GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _GROQ_BASE_URL, json=payload, headers=headers, timeout=timeout
        )
    except requests.exceptions.Timeout as exc:
        raise GroqAvailabilityError(f"Request timed out: {exc}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise GroqAvailabilityError(f"Connection error: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise GroqNonRetryableError(f"Groq request failed: {exc}") from exc

    if response.ok:
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    payload = _parse_error_payload(response)
    detail = _error_message(payload)

    if _is_availability_failure(response.status_code, payload):
        raise GroqAvailabilityError(
            f"HTTP {response.status_code} for model={model}: {detail}"
        )

    raise GroqNonRetryableError(
        f"HTTP {response.status_code} for model={model}: {detail}"
    )


def call_gemini(
    model_name=None,
    messages=None,
    temperature=0.0,
    max_tokens=600,
    timeout=60,
    disable_reasoning=False,
):
    """
    Call Groq chat completions with automatic model fallback.

    `model_name` is treated as the preferred model; on availability failures
    the request is retried with the next model in GROQ_MODEL_FALLBACK_CHAIN.

    Set `disable_reasoning=True` for internal/backend steps (query rewrite,
    routing, evaluation). User-facing chat responses should leave this False
    so reasoning can be shown in the UI when enabled.
    """
    if messages is None:
        raise GroqNonRetryableError("messages is required")

    preferred = (model_name or "").strip() or None
    chain = build_model_chain(preferred)
    if not chain:
        chain = list(GROQ_MODEL_FALLBACK_CHAIN)

    last_error: Exception | None = None

    for index, model in enumerate(chain):
        try:
            result = _call_groq_once(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                disable_reasoning=disable_reasoning,
            )
            if index == 0:
                logger.info("Groq request served by model=%s", model)
                print(f"[Groq] Request served by model={model}")
            else:
                logger.warning(
                    "Groq fallback: preferred=%s failed; served by model=%s",
                    preferred or chain[0],
                    model,
                )
                print(
                    f"[Groq] Fallback: preferred={preferred or chain[0]} "
                    f"unavailable — served by model={model}"
                )
            return result
        except GroqNonRetryableError:
            raise
        except GroqAvailabilityError as exc:
            last_error = exc
            logger.warning("Groq model unavailable model=%s error=%s", model, exc)
            print(f"[Groq] Model {model} unavailable ({exc}); trying next in chain…")
            if index < len(chain) - 1:
                time.sleep(0.5)
            continue

    chain_label = ", ".join(chain)
    raise RuntimeError(
        f"All Groq models failed ({chain_label}). "
        f"Last error: {last_error}"
    )
