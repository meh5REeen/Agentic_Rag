"""
SSE-compatible entry point for the agent harness.

Yields the same event shapes as pipeline.run_pipeline_stream:
  {"type":"step","step":{...}}
  {"type":"done","response":...,"file":...,"rag_used":...,"citations":[...]}

Does NOT modify or call run_pipeline_stream — only reuses lower-level pieces
via tools / parent helpers.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Iterator

from db import (
    add_new_message,
    get_conversation_project_id,
    load_conversation_history,
    save_trace_steps,
)
from tools.file_detector import detect_file_request
from tools.file_generator import generate_file
from agent_harness.memory import RUN_STORE
from agent_harness.parent import run_parent

log = logging.getLogger(__name__)


def run_agent_harness_stream(
    user_query: str,
    session_id: str = "default",
    use_web_search: bool = False,
) -> Iterator[dict]:
    """
    Parent/sub-agent orchestration as an SSE event generator.
    """
    conversation_history = load_conversation_history(session_id)
    project_id = get_conversation_project_id(session_id)
    file_request = detect_file_request(user_query)

    # Buffer every UI-visible step so we can persist them after the assistant
    # message row exists (FK). Same payloads as SSE — never raw transcripts.
    buffered_steps: list[dict] = []

    history_step = {
        "type": "history",
        "label": "Loaded conversation history (parent only)",
        "message_count": len(conversation_history or []),
    }
    buffered_steps.append(history_step)
    yield {"type": "step", "step": history_step}

    step_queue: queue.Queue = queue.Queue()
    result_box: dict = {}

    def _on_event(step: dict):
        step_queue.put({"type": "step", "step": step})

    def _worker():
        try:
            answer, citations, memories, run_id = run_parent(
                query=user_query,
                session_id=session_id,
                conversation_history=conversation_history,
                project_id=project_id,
                allow_web=use_web_search,
                on_event=_on_event,
            )
            result_box["answer"] = answer
            result_box["citations"] = citations
            result_box["memories"] = memories
            result_box["run_id"] = run_id
            result_box["rag_used"] = any(m.subtask_type == "rag" for m in memories)
        except Exception as exc:
            log.exception("Agent harness parent failed")
            result_box["error"] = str(exc)
        finally:
            step_queue.put(None)  # sentinel

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    while True:
        item = step_queue.get()
        if item is None:
            break
        step = item.get("step")
        if isinstance(step, dict):
            buffered_steps.append(step)
        yield item

    thread.join(timeout=5)

    if result_box.get("error"):
        yield {"type": "error", "error": f"Agent harness error: {result_box['error']}"}
        return

    answer = result_box.get("answer") or "I was unable to generate a response. Please try again."
    citations = result_box.get("citations") or []
    rag_used = bool(result_box.get("rag_used"))
    run_id = result_box.get("run_id")

    file_info = None
    if file_request.get("generate"):
        try:
            file_info = generate_file(
                query=user_query,
                file_type=file_request.get("file_type") or "pdf",
                content=answer,
            )
            file_step = {
                "type": "file",
                "label": "Generated file",
                "file_type": file_info.get("file_type"),
                "filename": file_info.get("filename"),
                "path": file_info.get("path"),
            }
            buffered_steps.append(file_step)
            yield {"type": "step", "step": file_step}
        except Exception as exc:
            log.exception("File generation after harness failed")
            file_step = {
                "type": "file",
                "label": "File generation failed",
                "error": str(exc),
            }
            buffered_steps.append(file_step)
            yield {"type": "step", "step": file_step}

    add_new_message(session_id, "user", user_query)
    assistant_message_id = add_new_message(session_id, "assistant", answer)

    try:
        save_trace_steps(assistant_message_id, buffered_steps, default_run_id=run_id)
    except Exception:
        log.exception(
            "Failed to persist harness trace_steps for message_id=%s",
            assistant_message_id,
        )

    # Drop in-memory memories for this run after the response is finalized.
    # Transcripts were already wiped inside each sub-agent.
    if run_id:
        RUN_STORE.clear_run(run_id)

    yield {
        "type": "done",
        "response": answer,
        "file": file_info,
        "rag_used": rag_used,
        "citations": citations,
        "agent_mode": True,
    }
