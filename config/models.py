"""Central Groq model IDs and fallback chain configuration."""
import logging
import os

logger = logging.getLogger(__name__)

# Ordered fallback chain — first entry is the preferred default for all roles.
GROQ_MODEL_FALLBACK_CHAIN = [
    "qwen/qwen3.6-27b",       # preferred — best quality for our use case
    "openai/gpt-oss-120b",    # stable production fallback
    "openai/gpt-oss-20b",     # fast, cheap fallback
]

DEFAULT_GROQ_MODEL = GROQ_MODEL_FALLBACK_CHAIN[0]

# Per-role env vars (optional override for the preferred / first model in chain).
ENV_ORCHESTRATOR_MODEL = "ORCHESTRATOR_MODEL"
ENV_REWRITER_MODEL = "REWRITER_MODEL"
ENV_EVALUATOR_MODEL = "EVALUATOR_MODEL"
ENV_RESPONSE_MODEL = "RESPONSE_MODEL"


def _is_deprecated_groq_model(model: str) -> bool:
    """Groq Llama 3.x IDs were moved off the free/developer tier (Aug 2026)."""
    normalized = model.strip().lower()
    return normalized.startswith("llama-3") or normalized.startswith("llama3")


def sanitize_groq_model(model: str | None) -> str | None:
    """Drop deprecated / empty overrides so callers fall back to the chain."""
    if not model:
        return None
    cleaned = model.strip()
    if not cleaned:
        return None
    if _is_deprecated_groq_model(cleaned):
        logger.warning(
            "Ignoring deprecated Groq model override %r; using %s",
            cleaned,
            DEFAULT_GROQ_MODEL,
        )
        return None
    return cleaned


def build_model_chain(preferred: str | None = None) -> list[str]:
    """
    Build an ordered list of Groq model IDs to try.
    `preferred` (or a role env override) is tried first, then the shared chain.
    """
    chain: list[str] = []
    preferred = sanitize_groq_model(preferred)
    if preferred:
        chain.append(preferred)
    for model in GROQ_MODEL_FALLBACK_CHAIN:
        if model not in chain:
            chain.append(model)
    return chain


def get_role_preferred_model(env_var: str) -> str:
    """Preferred model for a pipeline role (env override or chain default)."""
    override = sanitize_groq_model(os.getenv(env_var))
    return override or DEFAULT_GROQ_MODEL


def get_role_model_chain(env_var: str) -> list[str]:
    """Full fallback chain for a pipeline role."""
    return build_model_chain(get_role_preferred_model(env_var))
