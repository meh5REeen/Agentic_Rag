"""
Parent agent: plan subtasks → dispatch parallel sub-agents → aggregate memories.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from llm_client import call_gemini
from orchestrator import get_snapshot
from agent_harness.config import (
    ALLOWED_SUBTASK_TYPES,
    ALLOWED_TOOLS,
    AGGREGATOR_MODEL,
    MAX_SUBAGENTS,
    PLANNER_MODEL,
)
from agent_harness.memory import RUN_STORE, SubAgentMemory
from agent_harness.subagent import SubAgentRunner

log = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip(text: str) -> str:
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def _extract_json(raw: str) -> dict | None:
    text = _strip(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _normalize_subtasks(raw_subtasks: list) -> list[dict]:
    normalized = []
    for i, item in enumerate(raw_subtasks or []):
        if not isinstance(item, dict):
            continue
        sub_type = str(item.get("type") or "answer").lower().strip()
        if sub_type not in ALLOWED_SUBTASK_TYPES:
            sub_type = "answer"
        tools = item.get("tools") or []
        if not isinstance(tools, list):
            tools = []
        tools = [t for t in tools if t in ALLOWED_TOOLS]
        if not tools:
            if sub_type == "rag":
                tools = ["retrieve", "answer"]
            elif sub_type == "web":
                tools = ["web_search", "answer"]
            else:
                tools = ["answer"]
        instruction = str(item.get("instruction") or "").strip()
        if not instruction:
            continue
        normalized.append({
            "id": str(item.get("id") or f"t{i+1}"),
            "type": sub_type,
            "instruction": instruction,
            "tools": tools,
        })
        if len(normalized) >= MAX_SUBAGENTS:
            break
    return normalized


def _fallback_plan(query: str, allow_web: bool) -> list[dict]:
    """If the planner LLM fails, run a single RAG subtask (+ optional web)."""
    plan = [{
        "id": "t1",
        "type": "rag",
        "instruction": query,
        "tools": ["retrieve", "answer"],
    }]
    if allow_web and len(plan) < MAX_SUBAGENTS:
        plan.append({
            "id": "t2",
            "type": "web",
            "instruction": query,
            "tools": ["web_search", "answer"],
        })
    return plan


def plan_subtasks(query: str, snapshot: str, allow_web: bool = False) -> list[dict]:
    """Ask the planner LLM to decompose the user query into bounded subtasks."""
    web_note = (
        "You MAY include at most one 'web' subtask."
        if allow_web
        else "Do NOT include 'web' subtasks."
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a parent planner. Decompose the user query into 1–"
                f"{MAX_SUBAGENTS} subtasks for isolated sub-agents.\n"
                "Return ONLY JSON:\n"
                '{"subtasks":[{"id":"t1","type":"rag|web|answer",'
                '"instruction":"...","tools":["retrieve","answer"]}]}\n'
                "Types:\n"
                "- rag: needs local documents (tools: retrieve, answer)\n"
                "- web: needs live internet (tools: web_search, answer)\n"
                "- answer: reasoning/synthesis with no retrieval (tools: answer)\n"
                f"{web_note}\n"
                "Each instruction must be self-contained — sub-agents get NO chat history."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Conversation snapshot (context only): {snapshot}\n\n"
                f"User query: {query}"
            ),
        },
    ]
    try:
        raw = call_gemini(
            model_name=PLANNER_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=500,
        )
        data = _extract_json(raw) or {}
        plan = _normalize_subtasks(data.get("subtasks") or [])
        if plan:
            return plan
        log.warning("Planner returned no usable subtasks; using fallback plan")
    except Exception as exc:
        log.warning("Planner failed (%s); using fallback plan", exc)
    return _fallback_plan(query, allow_web=allow_web)


def aggregate_memories(query: str, memories: list[SubAgentMemory]) -> str:
    """Build the final user-facing answer from compact memories only."""
    useful = [m for m in memories if m.status in ("ok", "empty") and m.result_summary]
    failed = [m for m in memories if m.status in ("error", "timeout")]

    if not useful and failed:
        return (
            "I tried to break this into subtasks, but every sub-agent failed. "
            "Please try again or rephrase your question."
        )
    if not useful:
        from response_generator import generate_safe_response
        return generate_safe_response(query)

    memory_block = []
    for m in memories:
        memory_block.append(
            f"- [{m.subtask_id} | {m.subtask_type} | {m.status}]\n"
            f"  Instruction: {m.instruction}\n"
            f"  Summary: {m.result_summary}"
        )
    messages = [
        {
            "role": "system",
            "content": (
                "You are the parent agent. Using ONLY the sub-agent memories below, "
                "write a clear final answer for the user. "
                "Do not invent facts that are not in the memories. "
                "If some subtasks failed, briefly note the gap. "
                "Keep citations like [Document N] when they appear in memories."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User query: {query}\n\n"
                f"Sub-agent memories:\n" + "\n".join(memory_block)
            ),
        },
    ]
    try:
        raw = call_gemini(
            model_name=AGGREGATOR_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=700,
        )
        text = _strip(raw)
        return text or "I gathered sub-agent results but could not assemble a final answer."
    except Exception as exc:
        log.exception("Aggregation failed")
        # Fallback: concatenate summaries.
        parts = [m.result_summary for m in useful if m.result_summary]
        parts.append(f"(Aggregation error: {exc})")
        return "\n\n".join(parts)


def merge_citations(memories: list[SubAgentMemory]) -> list[dict]:
    """Merge citation maps from RAG memories; re-index for the final answer."""
    merged = []
    seen = set()
    index = 1
    for m in memories:
        for c in m.citations or []:
            key = (
                c.get("document_id"),
                c.get("source") or c.get("sourceFile"),
                c.get("page"),
                c.get("chunkId"),
            )
            if key in seen:
                continue
            seen.add(key)
            item = dict(c)
            item["index"] = index
            merged.append(item)
            index += 1
    return merged


def dispatch_parallel(
    subtasks: list[dict],
    project_id,
    on_memory: Optional[Callable[[SubAgentMemory], None]] = None,
) -> list[SubAgentMemory]:
    """Run sub-agents in parallel; optionally notify as each memory arrives."""
    runner = SubAgentRunner()
    memories: list[SubAgentMemory] = []

    if not subtasks:
        return memories

    with ThreadPoolExecutor(max_workers=min(len(subtasks), MAX_SUBAGENTS)) as pool:
        futures = {
            pool.submit(runner.run_with_timeout, subtask, project_id): subtask
            for subtask in subtasks
        }
        for fut in as_completed(futures):
            memory = fut.result()
            memories.append(memory)
            if on_memory:
                on_memory(memory)

    # Stable order by original plan ids.
    order = {s["id"]: i for i, s in enumerate(subtasks)}
    memories.sort(key=lambda m: order.get(m.subtask_id, 999))
    return memories


def run_parent(
    query: str,
    session_id: str,
    conversation_history,
    project_id=None,
    allow_web: bool = False,
    on_event: Optional[Callable[[dict], None]] = None,
) -> tuple[str, list[dict], list[SubAgentMemory], str]:
    """
    Full parent cycle. Returns (answer, citations, memories, run_id).
    on_event receives step-like dicts for SSE (type/label/... already shaped).
    """
    def emit(step: dict):
        if on_event:
            on_event(step)

    run_id = RUN_STORE.start_run(session_id=session_id)
    snapshot = get_snapshot(conversation_history)

    emit({
        "type": "agent_plan",
        "label": "Parent planning subtasks",
        "run_id": run_id,
        "snapshot": snapshot[:300] if isinstance(snapshot, str) else "",
    })

    subtasks = plan_subtasks(query, snapshot, allow_web=allow_web)
    emit({
        "type": "agent_plan",
        "label": f"Planned {len(subtasks)} subtask(s)",
        "run_id": run_id,
        "subtasks": [
            {"id": s["id"], "type": s["type"], "instruction": s["instruction"], "tools": s["tools"]}
            for s in subtasks
        ],
    })

    for s in subtasks:
        emit({
            "type": "subagent_start",
            "label": f"Sub-agent {s['id']} started ({s['type']})",
            "run_id": run_id,
            "subtask_id": s["id"],
            "subtask_type": s["type"],
            "instruction": s["instruction"],
            "tools": s["tools"],
        })

    def _on_memory(memory: SubAgentMemory):
        RUN_STORE.write(run_id, memory)
        emit({
            "type": "subagent_memory",
            "label": f"Sub-agent {memory.subtask_id} memory ({memory.status})",
            "run_id": run_id,
            "subtask_id": memory.subtask_id,
            "subtask_type": memory.subtask_type,
            "status": memory.status,
            "result_summary": memory.result_summary,
            "sources_used": memory.sources_used,
            "error": memory.error,
        })

    memories = dispatch_parallel(subtasks, project_id, on_memory=_on_memory)

    emit({
        "type": "agent_aggregate",
        "label": "Aggregating sub-agent memories",
        "run_id": run_id,
        "memory_count": len(memories),
        "statuses": {m.subtask_id: m.status for m in memories},
    })

    answer = aggregate_memories(query, memories)
    citations = merge_citations(memories)

    emit({
        "type": "agent_aggregate",
        "label": "Final answer assembled from memories",
        "run_id": run_id,
        "citation_count": len(citations),
    })

    return answer, citations, memories, run_id
