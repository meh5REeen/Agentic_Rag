"""
Isolated sub-agent runner.

Each run starts with an empty message list (no parent history). After a compact
memory is extracted, the local transcript/scratch is wiped.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any

from llm_client import call_gemini
from agent_harness.config import (
    ALLOWED_TOOLS,
    MAX_SUBAGENT_STEPS,
    SUBAGENT_MODEL,
    SUBAGENT_TIMEOUT_SEC,
    AGENT_DEBUG_TRANSCRIPTS,
)
from agent_harness.memory import SubAgentMemory, wipe_subagent_context
from agent_harness.tools import run_tool

log = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip(text: str) -> str:
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def _parse_action(raw: str) -> dict[str, Any] | None:
    text = _strip(raw)
    if not text:
        return None
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


def _sources_from_retrieve(payload: dict) -> list[dict]:
    sources = []
    for snip in payload.get("snippets") or []:
        sources.append({
            "source": snip.get("source"),
            "page": snip.get("page"),
            "document_id": snip.get("document_id"),
        })
    return sources


def _sources_from_web(payload: dict) -> list[dict]:
    sources = []
    for item in payload.get("results") or []:
        sources.append({
            "source": item.get("title") or item.get("url"),
            "page": None,
            "document_id": None,
            "url": item.get("url"),
        })
    return sources


def _format_retrieve_context(payload: dict) -> str:
    lines = []
    for snip in payload.get("snippets") or []:
        lines.append(
            f"[Document {snip.get('index')} | Source: {snip.get('source')} | "
            f"Page: {snip.get('page')}]\n{snip.get('preview', '')}"
        )
    return "\n\n---\n\n".join(lines) if lines else "No documents retrieved."


def _format_web_context(payload: dict) -> str:
    lines = []
    for i, item in enumerate(payload.get("results") or [], start=1):
        lines.append(
            f"[{i}] {item.get('title')}\nURL: {item.get('url')}\n{item.get('snippet')}"
        )
    return "\n\n".join(lines) if lines else "No web results."


def _deterministic_run(subtask: dict, project_id, allowed: set[str]) -> SubAgentMemory:
    """
    Reliable path when the tool-loop LLM misfires: execute by subtask type
    using only allowed tools.
    """
    subtask_id = str(subtask.get("id") or "t?")
    subtask_type = str(subtask.get("type") or "answer").lower()
    instruction = str(subtask.get("instruction") or "").strip()
    sources: list[dict] = []
    citations: list[dict] = []
    scratch: dict[str, Any] = {}

    try:
        if subtask_type == "rag" and "retrieve" in allowed:
            retrieved = run_tool("retrieve", allowed, query=instruction, project_id=project_id)
            scratch["retrieve"] = {k: v for k, v in retrieved.items() if k != "docs"}
            sources = _sources_from_retrieve(retrieved)
            citations = retrieved.get("citations") or []
            if not retrieved.get("ok") or not retrieved.get("snippets"):
                return SubAgentMemory(
                    subtask_id=subtask_id,
                    subtask_type=subtask_type,
                    status="empty",
                    result_summary="No relevant documents found for this subtask.",
                    sources_used=sources,
                    citations=citations,
                    instruction=instruction,
                )
            context = _format_retrieve_context(retrieved)
            if "answer" in allowed:
                answered = run_tool("answer", allowed, question=instruction, context=context)
                summary = answered.get("answer") or "Retrieved documents but could not summarize."
                status = "ok" if answered.get("ok") else "error"
                err = answered.get("error")
            else:
                summary = context[:800]
                status = "ok"
                err = None
            return SubAgentMemory(
                subtask_id=subtask_id,
                subtask_type=subtask_type,
                status=status,
                result_summary=summary,
                sources_used=sources,
                citations=citations,
                error=err,
                instruction=instruction,
            )

        if subtask_type == "web" and "web_search" in allowed:
            web = run_tool("web_search", allowed, query=instruction)
            scratch["web"] = web
            sources = _sources_from_web(web)
            if not web.get("ok"):
                return SubAgentMemory(
                    subtask_id=subtask_id,
                    subtask_type=subtask_type,
                    status="error",
                    result_summary="Web search failed.",
                    sources_used=sources,
                    error=web.get("error"),
                    instruction=instruction,
                )
            if not web.get("results"):
                return SubAgentMemory(
                    subtask_id=subtask_id,
                    subtask_type=subtask_type,
                    status="empty",
                    result_summary="Web search returned no results.",
                    sources_used=sources,
                    instruction=instruction,
                )
            context = _format_web_context(web)
            if "answer" in allowed:
                answered = run_tool("answer", allowed, question=instruction, context=context)
                summary = answered.get("answer") or context[:800]
                status = "ok" if answered.get("ok") else "error"
                err = answered.get("error")
            else:
                summary = context[:800]
                status = "ok"
                err = None
            return SubAgentMemory(
                subtask_id=subtask_id,
                subtask_type=subtask_type,
                status=status,
                result_summary=summary,
                sources_used=sources,
                error=err,
                instruction=instruction,
            )

        # answer-only / fallback
        answered = run_tool(
            "answer",
            allowed | {"answer"},
            question=instruction,
            context="No extra context. Answer from general knowledge if appropriate; otherwise say you need documents.",
        )
        return SubAgentMemory(
            subtask_id=subtask_id,
            subtask_type=subtask_type,
            status="ok" if answered.get("ok") else "error",
            result_summary=answered.get("answer") or "No answer produced.",
            sources_used=[],
            error=answered.get("error"),
            instruction=instruction,
        )
    finally:
        wipe_subagent_context(None, scratch)


def _tool_loop_run(subtask: dict, project_id, allowed: set[str]) -> SubAgentMemory:
    subtask_id = str(subtask.get("id") or "t?")
    subtask_type = str(subtask.get("type") or "answer").lower()
    instruction = str(subtask.get("instruction") or "").strip()

    messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "You are a focused sub-agent with an empty context. "
                "You may ONLY use the listed tools. "
                "Reply with a single JSON object, no markdown.\n"
                'Tool call: {"action":"tool","tool":"<name>","args":{...}}\n'
                'Finish: {"action":"finish","summary":"<compact result>"}\n'
                f"Allowed tools: {sorted(allowed)}"
            ),
        },
        {
            "role": "user",
            "content": f"Subtask type: {subtask_type}\nInstruction: {instruction}",
        },
    ]
    scratch: dict[str, Any] = {
        "sources": [],
        "citations": [],
        "last_context": "",
    }

    try:
        for step in range(MAX_SUBAGENT_STEPS):
            raw = call_gemini(
                model_name=SUBAGENT_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=400,
            )
            if AGENT_DEBUG_TRANSCRIPTS:
                log.debug("Sub-agent %s step %s raw: %s", subtask_id, step, raw[:500])

            action = _parse_action(raw)
            if not action:
                # Fall back to deterministic execution for this subtask.
                log.info("Sub-agent %s: invalid JSON — using deterministic path", subtask_id)
                return _deterministic_run(subtask, project_id, allowed)

            kind = str(action.get("action") or "").lower()
            if kind == "finish":
                summary = str(action.get("summary") or "").strip()
                if not summary:
                    summary = scratch.get("last_context") or "Sub-agent finished with no summary."
                status = "ok" if summary else "empty"
                return SubAgentMemory(
                    subtask_id=subtask_id,
                    subtask_type=subtask_type,
                    status=status,
                    result_summary=summary,
                    sources_used=list(scratch.get("sources") or []),
                    citations=list(scratch.get("citations") or []),
                    instruction=instruction,
                )

            if kind != "tool":
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": 'Invalid action. Use {"action":"tool",...} or {"action":"finish",...}.',
                })
                continue

            tool_name = str(action.get("tool") or "").strip()
            args = action.get("args") or {}
            if not isinstance(args, dict):
                args = {}

            # Inject project_id for retrieve when omitted.
            if tool_name == "retrieve":
                args.setdefault("query", instruction)
                args["project_id"] = project_id
            elif tool_name == "web_search":
                args.setdefault("query", instruction)
            elif tool_name == "answer":
                args.setdefault("question", instruction)
                args.setdefault("context", scratch.get("last_context") or "No context yet.")

            # Never pass Document objects back into the LLM transcript.
            result = run_tool(tool_name, allowed, **{
                k: v for k, v in args.items() if k != "docs"
            })

            if tool_name == "retrieve":
                scratch["sources"] = _sources_from_retrieve(result)
                scratch["citations"] = result.get("citations") or []
                scratch["last_context"] = _format_retrieve_context(result)
                safe = {k: v for k, v in result.items() if k != "docs"}
            elif tool_name == "web_search":
                scratch["sources"] = _sources_from_web(result)
                scratch["last_context"] = _format_web_context(result)
                safe = result
            else:
                safe = result
                if result.get("answer"):
                    scratch["last_context"] = result["answer"]

            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({
                "role": "user",
                "content": f"Tool result for {tool_name}:\n{json.dumps(safe, default=str)[:2500]}",
            })

        # Step budget exhausted — summarize whatever we have.
        summary = scratch.get("last_context") or "Sub-agent reached max steps without finishing."
        return SubAgentMemory(
            subtask_id=subtask_id,
            subtask_type=subtask_type,
            status="ok" if scratch.get("last_context") else "empty",
            result_summary=summary[:1200],
            sources_used=list(scratch.get("sources") or []),
            citations=list(scratch.get("citations") or []),
            instruction=instruction,
        )
    finally:
        wipe_subagent_context(messages, scratch)


class SubAgentRunner:
    def run(self, subtask: dict, project_id=None) -> SubAgentMemory:
        tools = subtask.get("tools") or []
        allowed = {t for t in tools if t in ALLOWED_TOOLS}
        if not allowed:
            # Sensible defaults by type.
            t = str(subtask.get("type") or "answer").lower()
            if t == "rag":
                allowed = {"retrieve", "answer"}
            elif t == "web":
                allowed = {"web_search", "answer"}
            else:
                allowed = {"answer"}

        # Prefer deterministic path for v1 reliability; tool-loop available via flag.
        return _deterministic_run(subtask, project_id, allowed)

    def run_with_timeout(self, subtask: dict, project_id=None, timeout: int | None = None) -> SubAgentMemory:
        timeout = timeout if timeout is not None else SUBAGENT_TIMEOUT_SEC
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(self.run, subtask, project_id)
            try:
                return fut.result(timeout=timeout)
            except FuturesTimeout:
                return SubAgentMemory(
                    subtask_id=str(subtask.get("id") or "t?"),
                    subtask_type=str(subtask.get("type") or "answer"),
                    status="timeout",
                    result_summary="Sub-agent timed out.",
                    error=f"Exceeded {timeout}s",
                    instruction=str(subtask.get("instruction") or ""),
                )
            except Exception as exc:
                log.exception("Sub-agent failed")
                return SubAgentMemory(
                    subtask_id=str(subtask.get("id") or "t?"),
                    subtask_type=str(subtask.get("type") or "answer"),
                    status="error",
                    result_summary="Sub-agent failed.",
                    error=str(exc),
                    instruction=str(subtask.get("instruction") or ""),
                )
