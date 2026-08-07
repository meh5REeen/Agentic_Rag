"""
Parent / ephemeral sub-agent harness.

Optional orchestration layer around the existing RAG pieces. Enable via the
UI "Agent mode" toggle (agent_mode=true on /api/chat). The classic
run_pipeline_stream path remains unchanged.
"""

from agent_harness.stream import run_agent_harness_stream
from agent_harness.memory import SubAgentMemory, RUN_STORE
from agent_harness.config import MAX_SUBAGENTS, MAX_SUBAGENT_STEPS, SUBAGENT_TIMEOUT_SEC

__all__ = [
    "run_agent_harness_stream",
    "SubAgentMemory",
    "RUN_STORE",
    "MAX_SUBAGENTS",
    "MAX_SUBAGENT_STEPS",
    "SUBAGENT_TIMEOUT_SEC",
]
