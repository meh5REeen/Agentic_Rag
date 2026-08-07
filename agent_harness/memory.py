"""
Structured sub-agent memory + per-run store.

Memories are compact summaries only. Full sub-agent transcripts live only
inside the runner's local variables and are wiped after memory extraction.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class SubAgentMemory:
    subtask_id: str
    subtask_type: str
    status: str  # ok | error | timeout | empty
    result_summary: str
    sources_used: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    instruction: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def wipe_subagent_context(messages: list | None, scratch: dict | None) -> None:
    """Drop local transcript/scratch so it cannot leak to parent or peers."""
    if messages is not None:
        messages.clear()
    if scratch is not None:
        scratch.clear()


class AgentRunStore:
    """
    In-process store keyed by run_id. Thread-safe for parallel sub-agents.
    Does not persist transcripts — only SubAgentMemory objects.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}

    def start_run(self, session_id: str | None = None) -> str:
        run_id = uuid.uuid4().hex
        with self._lock:
            self._runs[run_id] = {
                "session_id": session_id,
                "memories": {},
            }
        return run_id

    def write(self, run_id: str, memory: SubAgentMemory) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(f"Unknown run_id: {run_id}")
            run["memories"][memory.subtask_id] = memory

    def read_all(self, run_id: str) -> list[SubAgentMemory]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return []
            return list(run["memories"].values())

    def clear_run(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)


# Process-wide store for active harness runs.
RUN_STORE = AgentRunStore()
