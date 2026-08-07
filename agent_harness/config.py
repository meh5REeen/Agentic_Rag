"""Configurable limits for the parent / sub-agent harness."""
import os

MAX_SUBAGENTS = int(os.getenv("MAX_SUBAGENTS", "3"))
MAX_SUBAGENT_STEPS = int(os.getenv("MAX_SUBAGENT_STEPS", "4"))
SUBAGENT_TIMEOUT_SEC = int(os.getenv("SUBAGENT_TIMEOUT_SEC", "60"))
AGENT_DEBUG_TRANSCRIPTS = os.getenv("AGENT_DEBUG_TRANSCRIPTS", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Planner / sub-agent / aggregator models (reuse existing env defaults).
PLANNER_MODEL = os.getenv("ORCHESTRATOR_MODEL", "llama-3.1-8b-instant")
SUBAGENT_MODEL = os.getenv("RESPONSE_MODEL", "llama-3.3-70b-versatile")
AGGREGATOR_MODEL = os.getenv("RESPONSE_MODEL", "llama-3.3-70b-versatile")

ALLOWED_SUBTASK_TYPES = ("rag", "web", "answer")
ALLOWED_TOOLS = ("retrieve", "web_search", "answer")
