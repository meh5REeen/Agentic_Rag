"""Configurable limits for the parent / sub-agent harness."""
import os

from config.models import (
    ENV_ORCHESTRATOR_MODEL,
    ENV_RESPONSE_MODEL,
    get_role_preferred_model,
)

MAX_SUBAGENTS = int(os.getenv("MAX_SUBAGENTS", "3"))
MAX_SUBAGENT_STEPS = int(os.getenv("MAX_SUBAGENT_STEPS", "4"))
SUBAGENT_TIMEOUT_SEC = int(os.getenv("SUBAGENT_TIMEOUT_SEC", "60"))
AGENT_DEBUG_TRANSCRIPTS = os.getenv("AGENT_DEBUG_TRANSCRIPTS", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Planner / sub-agent / aggregator models (shared config + optional env override).
PLANNER_MODEL = get_role_preferred_model(ENV_ORCHESTRATOR_MODEL)
SUBAGENT_MODEL = get_role_preferred_model(ENV_RESPONSE_MODEL)
AGGREGATOR_MODEL = get_role_preferred_model(ENV_RESPONSE_MODEL)

ALLOWED_SUBTASK_TYPES = ("rag", "web", "answer")
ALLOWED_TOOLS = ("retrieve", "web_search", "answer")
