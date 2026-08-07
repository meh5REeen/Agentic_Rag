"""
Scoped tool wrappers around existing project callables.

Sub-agents only receive the tools listed on their subtask — never the full set.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from llm_client import call_gemini
from agent_harness.config import SUBAGENT_MODEL

log = logging.getLogger(__name__)


def _strip(text: str) -> str:
    if not text:
        return ""
    if "</think>" in text:
        text = text.split("</think>", 1)[-1]
    return text.strip()


def tool_retrieve(query: str, project_id=None, final_k: int = 5) -> dict[str, Any]:
    """Retrieve ranked chunks from Chroma (existing retrieval — no scoring changes)."""
    from retrieval import load_vectorstore, retrieve_with_scores
    from pipeline import _build_citation_map, _resolve_document_id

    vectorstore = load_vectorstore()
    ranked = retrieve_with_scores(
        vectorstore, query, project_id=project_id, final_k=final_k
    )
    docs = [item["doc"] for item in ranked]
    snippets = []
    for i, item in enumerate(ranked, start=1):
        doc = item["doc"]
        md = doc.metadata or {}
        document_id = _resolve_document_id(md)
        snippets.append({
            "index": i,
            "source": md.get("source"),
            "page": md.get("page"),
            "document_id": document_id,
            "score": round(float(item["score"]), 4),
            "preview": (doc.page_content or "")[:300],
        })
    citations = _build_citation_map(docs)
    return {
        "ok": True,
        "count": len(snippets),
        "snippets": snippets,
        "docs": docs,
        "citations": citations,
    }


def tool_web_search(query: str, top_k: int = 3) -> dict[str, Any]:
    from web_search_mcp import get_web_search_tool

    search = get_web_search_tool()
    if not search.is_available():
        return {"ok": False, "error": "Web search unavailable (missing TAVILY_API_KEY).", "results": []}
    try:
        results = search.search(query, top_k=top_k)
        return {
            "ok": True,
            "results": [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "snippet": r.get("snippet"),
                }
                for r in results
            ],
        }
    except Exception as exc:
        log.exception("Web search tool failed")
        return {"ok": False, "error": str(exc), "results": []}


def tool_answer(question: str, context: str) -> dict[str, Any]:
    """Short LLM answer using only the provided context string (no chat history)."""
    messages = [
        {
            "role": "system",
            "content": (
                "Answer using only the provided context. Be concise. "
                "If the context is insufficient, say what is missing. "
                "Cite document indexes like [Document 1] when context uses them."
            ),
        },
        {
            "role": "user",
            "content": f"Context:\n{context}\n\nQuestion: {question}",
        },
    ]
    try:
        raw = call_gemini(
            model_name=SUBAGENT_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=500,
        )
        text = _strip(raw)
        return {"ok": True, "answer": text or "No answer produced."}
    except Exception as exc:
        log.exception("Answer tool failed")
        return {"ok": False, "error": str(exc), "answer": ""}


TOOL_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "retrieve": tool_retrieve,
    "web_search": tool_web_search,
    "answer": tool_answer,
}


def run_tool(name: str, allowed: set[str], **kwargs) -> dict[str, Any]:
    if name not in allowed:
        return {"ok": False, "error": f"Tool '{name}' is not allowed for this sub-agent."}
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {"ok": False, "error": f"Unknown tool '{name}'."}
    return fn(**kwargs)
