import os
import re
import logging
from dotenv import load_dotenv
from db import (
    load_conversation_history,
    add_new_message,
    create_conversation,
    get_or_create_user_id,
    get_conversation_project_id,
    get_document_id_by_filename,
)
from query_rewriter import rewrite_query, rewrite_query_with_feedback
from orchestrator import needs_rag
from retrieval import load_vectorstore, retrieve, retrieve_with_scores
from evaluator import evaluate_documents
from response_generator import (
    generate_direct_response,
    generate_grounded_response,
    generate_safe_response
)
from web_search_mcp import get_web_search_tool
from tools.file_generator import generate_file
from tools.file_detector import detect_file_request

load_dotenv()

log = logging.getLogger(__name__)

MAX_RETRIES = 3       

DOC_PREVIEW_CHARS = 220
WEB_SEARCH_QUERY_RE = re.compile(
    r'\b(web search|search web|search:|web:|google|bing|tavily|today|latest|news|weather|current|release|upcoming|stock|price|who won|what happened|recent)\b',
    re.IGNORECASE
)

print("Loading vectorstore...")
vectorstore = load_vectorstore()
print("Vectorstore ready.\n")

def strip_thinking(text: str) -> str:
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'Thinking Process:.*', '', text, flags=re.DOTALL)
    
    return text.strip()


class PipelineTrace:
    """Collects a step-by-step record of what run_pipeline did for one query."""

    def __init__(self):
        self.steps = []
        self.rag_used = False

    def add(self, step_type, label, **data):
        self.steps.append({"type": step_type, "label": label, **data})
        return self.steps[-1]

    def to_dict(self):
        return {"steps": self.steps, "rag_used": self.rag_used}


def _viewer_url(document_id, page=None, chunk_id=None):
    """Build /docs/<id> URL with optional page (and chunk) query params."""
    if not document_id:
        return None
    url = f"/docs/{document_id}"
    params = []
    if page is not None and page != "?":
        try:
            params.append(f"page={int(page)}")
        except (TypeError, ValueError):
            pass
    if chunk_id is not None:
        params.append(f"chunk={chunk_id}")
    if params:
        url += "?" + "&".join(params)
    return url


def _resolve_document_id(md):
    """
    Prefer metadata document_id; for batch-ingested chunks that lack it,
    look up the documents row by source filename (display/linking only).
    """
    document_id = md.get("document_id")
    if document_id is not None and document_id != "":
        return document_id

    source = md.get("source") or ""
    filename = os.path.basename(source) if source else ""
    if not filename and md.get("file_path"):
        filename = os.path.basename(md.get("file_path"))
    if not filename:
        return None

    try:
        return get_document_id_by_filename(filename)
    except Exception as exc:
        log.warning("Could not resolve document_id for %s: %s", filename, exc)
        return None


def _serialize_ranked_docs(ranked_docs):
    serialized = []
    for item in ranked_docs:
        doc = item["doc"]
        content = doc.page_content or ""
        md = doc.metadata or {}
        document_id = _resolve_document_id(md)
        page = md.get("page")
        chunk_id = md.get("chunk_index") if md.get("chunk_index") is not None else md.get("chunk_id")
        serialized.append({
            "document_id": document_id,
            "source": md.get("source"),
            "page": page,
            "chunkId": chunk_id,
            "file_path": md.get("file_path"),
            "source_url": md.get("source_url"),
            "viewer_url": _viewer_url(document_id, page, chunk_id),
            "score": round(float(item["score"]), 4),
            "preview": content[:DOC_PREVIEW_CHARS],
            "truncated": len(content) > DOC_PREVIEW_CHARS,
        })
    return serialized


def _build_citation_map(retrieved_docs):
    """
    Returns structured citation metadata where index aligns with
    [Document N] / [Document N | Source: ... | Page: ...] in LLM output.
    """
    citations = []
    for i, doc in enumerate(retrieved_docs, start=1):
        md = doc.metadata or {}
        document_id = _resolve_document_id(md)
        source_url = md.get("source_url")
        page = md.get("page")
        chunk_id = md.get("chunk_index") if md.get("chunk_index") is not None else md.get("chunk_id")
        file_path = md.get("file_path")
        viewer_url = _viewer_url(document_id, page, chunk_id)
        url = source_url or viewer_url
        item = {
            "index": i,
            "docId": document_id,
            "document_id": document_id,
            "sourceFile": md.get("source"),
            "source": md.get("source"),
            "page": page,
            "chunkId": chunk_id,
            "file_path": file_path,
            "fileUrl": viewer_url,
            "url": url,
        }
        citations.append(item)

        # Sanity check: citation index must map to a real source document.
        if not item["source"]:
            log.warning("Citation %s has missing source metadata: %s", i, md)
        if not item["url"]:
            log.warning("Citation %s has no resolvable URL: %s", i, md)
        else:
            log.info(
                "Citation map: [Document %s] -> source=%s document_id=%s url=%s",
                i, item["source"], document_id, url,
            )
    return citations


def _generate_requested_file(user_query, file_request, response, trace):
    """
    Shared by both the direct-response and RAG-grounded branches: builds the
    file the user asked for and returns (file_info, step_dict).

    generate_file() now returns a dict — {"path", "filename", "file_type",
    "extension"} — not a bare path string, so callers get the filename and
    extension too (useful for building a download link) instead of having
    to re-derive them from the path.
    """
    file_info = generate_file(
        query=user_query,
        file_type=file_request["file_type"],
        content=response
    )
    step = trace.add(
        "file",
        "Generated file",
        file_type=file_info["file_type"],
        filename=file_info["filename"],
        path=file_info["path"]
    )
    return file_info, step


def _finish(user_query, file_request, response, trace, session_id, citations=None):
    """
    Single exit point for the pipeline, used by every branch (web search,
    direct answer, grounded answer, fallback/safe response — all of them).

    Handles, in one place:
      - optional file generation (checked once here, not per-branch)
      - saving the user + assistant messages to the DB
      - building the final "done" event

    Returns a list of events to yield (a "file" step if a file was generated,
    followed by exactly one "done" event) — so callers just do:

        for evt in _finish(...):
            yield evt
        return
    """
    events = []
    file_info = None

    if file_request["generate"]:
        file_info, file_step = _generate_requested_file(user_query, file_request, response, trace)
        events.append({"type": "step", "step": file_step})

    add_new_message(session_id, "user", user_query)
    add_new_message(session_id, "assistant", response)

    events.append({
        "type": "done",
        "response": response,
        "file": file_info,
        "rag_used": trace.rag_used,
        "citations": citations or [],
    })
    return events


def _run_pipeline_steps(user_query, session_id="default", use_web_search=False):
    """
    Core pipeline logic as a generator. See module docstring / _finish() for
    event shapes. File generation is no longer duplicated per-branch — every
    branch that produces a final response routes through _finish(), which is
    the single place that checks file_request and generates a file if asked.
    """
    print(f"USER QUERY: {user_query}")
    trace = PipelineTrace()
    file_request = detect_file_request(user_query)

    print("\n[Step 1] Loading conversation history...")
    conversation_history = load_conversation_history(session_id)
    step = trace.add(
        "history",
        "Loaded conversation history",
        message_count=len(conversation_history or [])
    )
    yield {"type": "step", "step": step}
    project_id = get_conversation_project_id(session_id)

    print("\n[Step 2] Rewriting query...")
    rewritten_query = rewrite_query(user_query, conversation_history)
    step = trace.add(
        "rewrite",
        "Rewrote query",
        original_query=user_query,
        rewritten_query=rewritten_query
    )
    yield {"type": "step", "step": step}

    if use_web_search:
        print("\n[Step 3] Performing web search...")
        web_search = get_web_search_tool()
        if not web_search.is_available():
            web_step = trace.add(
                "web_search",
                "Web search unavailable",
                query=user_query,
                available=False,
                reason="api_key_missing"
            )
            yield {"type": "step", "step": web_step}
        else:
            try:
                results = web_search.search(user_query, top_k=3)
            except Exception as exc:
                web_step = trace.add(
                    "web_search",
                    "Web search failed",
                    query=user_query,
                    available=True,
                    error=str(exc),
                    results=[]
                )
                yield {"type": "step", "step": web_step}
                print(f"Web search failed: {exc}")
                results = []

            web_step = trace.add(
                "web_search",
                "Performed web search",
                query=user_query,
                available=True,
                results=[
                    {"title": item.get("title"), "url": item.get("url"), "snippet": item.get("snippet")}
                    for item in results
                ]
            )
            yield {"type": "step", "step": web_step}

            if results:
                response_lines = []
                for idx, item in enumerate(results, start=1):
                    title = item.get("title") or item.get("url") or f"Result {idx}"
                    url = item.get("url") or ""
                    snippet = item.get("snippet") or ""
                    result_text = f"{idx}. [{title}]({url})"
                    if snippet:
                        result_text += f"\n\n{snippet}"
                    response_lines.append(result_text)

                response = "Web search results:\n\n" + "\n\n".join(response_lines)
                response = strip_thinking(response)

                step = trace.add("generate", "Generated web search response", response_type="web")
                yield {"type": "step", "step": step}

                for evt in _finish(user_query, file_request, response, trace, session_id):
                    yield evt
                return
            else:
                print("Web search returned no results; continuing with normal response flow.")

    print("\n[Step 4] Orchestrating...")
    rag_needed = needs_rag(rewritten_query, conversation_history)
    step = trace.add("orchestrate", "Decided whether retrieval is needed", rag_needed=rag_needed)
    yield {"type": "step", "step": step}

    if not rag_needed:
        print("\n[Step 5] Generating direct response...")
        response = generate_direct_response(user_query, conversation_history)
        response = strip_thinking(response)
        if not response.strip():
            response = "I was unable to generate a response. Please try again."

        step = trace.add("generate", "Generated direct response", response_type="direct")
        yield {"type": "step", "step": step}

        for evt in _finish(user_query, file_request, response, trace, session_id):
            yield evt
        return

    print("\n[Step 4] RAG path selected. Starting retrieval...")
    trace.rag_used = True

    current_rewritten_query = rewritten_query
    retry_count = 0

    while retry_count <= MAX_RETRIES:

        print(f"\n[Step 5] Retrieving documents (attempt {retry_count + 1}/{MAX_RETRIES + 1})...")
        ranked_docs = retrieve_with_scores(vectorstore, current_rewritten_query, project_id=project_id)
        retrieved_docs = [item["doc"] for item in ranked_docs]
        step = trace.add(
            "retrieval",
            f"Retrieved documents (attempt {retry_count + 1})",
            attempt=retry_count + 1,
            query=current_rewritten_query,
            documents=_serialize_ranked_docs(ranked_docs)
        )
        yield {"type": "step", "step": step}

        print("\n[Step 6] Evaluating retrieved documents...")
        evaluation = evaluate_documents(
            original_query=user_query,
            rewritten_query=current_rewritten_query,
            retrieved_docs=retrieved_docs
        )
        step = trace.add(
            "evaluation",
            f"Evaluated relevance (attempt {retry_count + 1})",
            attempt=retry_count + 1,
            relevant=evaluation["relevant"],
            feedback=evaluation.get("feedback")
        )
        yield {"type": "step", "step": step}

        if evaluation["relevant"]:
            print("\n[Step 7] Documents are relevant. Generating grounded response...")
            response = generate_grounded_response(
                original_query=user_query,
                rewritten_query=current_rewritten_query,
                retrieved_docs=retrieved_docs,
                conversation_history=conversation_history
            )
            response = strip_thinking(response)
            if not response.strip():
                response = "I was unable to generate a response. Please try again."

            step = trace.add("generate", "Generated grounded response", response_type="grounded", attempt=retry_count + 1)
            yield {"type": "step", "step": step}

            citations = _build_citation_map(retrieved_docs)
            for evt in _finish(user_query, file_request, response, trace, session_id, citations=citations):
                yield evt
            return

        print("\n[Step 8] Documents not relevant.")
        print(f"  Retries used: {retry_count}/{MAX_RETRIES}")

        if retry_count >= MAX_RETRIES:
            print("\n[Step 9] Retry limit reached. Returning safe response...")
            response = generate_safe_response(user_query)
            response = strip_thinking(response)
            if not response.strip():
                response = "I was unable to generate a response. Please try again."

            step = trace.add(
                "fallback",
                "Retry limit reached — returned a safe fallback response",
                response_type="safe",
                reason="retry_limit_reached",
                retries_used=retry_count
            )
            yield {"type": "step", "step": step}

            for evt in _finish(user_query, file_request, response, trace, session_id):
                yield evt
            return

        retry_count += 1
        print(f"\n[Step 9] Retrying with improved query (attempt {retry_count})...")

        current_rewritten_query = rewrite_query_with_feedback(
            original_query=user_query,
            rewritten_query=current_rewritten_query,
            feedback=evaluation["feedback"],
            conversation_history=conversation_history
        )
        step = trace.add(
            "refine",
            f"Refined query using evaluator feedback (attempt {retry_count})",
            attempt=retry_count,
            new_query=current_rewritten_query
        )
        yield {"type": "step", "step": step}

    response = generate_safe_response(user_query)
    response = strip_thinking(response)
    if not response.strip():
        response = "I was unable to generate a response. Please try again."

    step = trace.add("fallback", "Returned a safe fallback response", response_type="safe", reason="loop_exit")
    yield {"type": "step", "step": step}

    for evt in _finish(user_query, file_request, response, trace, session_id):
        yield evt

# def _run_pipeline_steps(user_query, session_id="default", use_web_search=False):
#     """
#     Core pipeline logic as a generator.

#     Yields one event per pipeline step, in real time, as that step completes:
#         {"type": "step", "step": <step dict>}

#     And finishes with exactly one terminal event:
#         {"type": "done", "response": <str>, "file": <dict | None>, "rag_used": <bool>}

#     `file` is the dict returned by generate_file() — {"path", "filename",
#     "file_type", "extension"} — or None if no file was requested.

#     All DB writes (saving the user + assistant messages) happen exactly where
#     they used to, so behavior/persistence is unchanged — only the timing of
#     when the trace info becomes visible to a caller has changed.
#     """
#     print(f"USER QUERY: {user_query}")
#     file_info = None
#     file_generated = False
#     trace = PipelineTrace()
#     file_request = detect_file_request(user_query)


#     print("\n[Step 1] Loading conversation history...")
#     conversation_history = load_conversation_history(session_id)
#     step = trace.add(
#         "history",
#         "Loaded conversation history",
#         message_count=len(conversation_history or [])
#     )
#     yield {"type": "step", "step": step}
#     project_id = get_conversation_project_id(session_id)

#     print("\n[Step 2] Rewriting query...")
#     rewritten_query = rewrite_query(user_query, conversation_history)
#     step = trace.add(
#         "rewrite",
#         "Rewrote query",
#         original_query=user_query,
#         rewritten_query=rewritten_query
#     )
#     yield {"type": "step", "step": step}

#     if use_web_search:
#         print("\n[Step 3] Performing web search...")
#         web_search = get_web_search_tool()
#         if not web_search.is_available():
#             web_step = trace.add(
#                 "web_search",
#                 "Web search unavailable",
#                 query=user_query,
#                 available=False,
#                 reason="api_key_missing"
#             )
#             yield {"type": "step", "step": web_step}
#         else:
#             try:
#                 results = web_search.search(user_query, top_k=3)
#             except Exception as exc:
#                 web_step = trace.add(
#                     "web_search",
#                     "Web search failed",
#                     query=user_query,
#                     available=True,
#                     error=str(exc),
#                     results=[]
#                 )
#                 yield {"type": "step", "step": web_step}
#                 print(f"Web search failed: {exc}")
#                 results = []

#             web_step = trace.add(
#                 "web_search",
#                 "Performed web search",
#                 query=user_query,
#                 available=True,
#                 results=[
#                     {
#                         "title": item.get("title"),
#                         "url": item.get("url"),
#                         "snippet": item.get("snippet"),
#                     }
#                     for item in results
#                 ]
#             )
#             yield {"type": "step", "step": web_step}

#             if results:
#                 response_lines = []
#                 for idx, item in enumerate(results, start=1):
#                     title = item.get("title") or item.get("url") or f"Result {idx}"
#                     url = item.get("url") or ""
#                     snippet = item.get("snippet") or ""
#                     result_text = f"{idx}. [{title}]({url})"
#                     if snippet:
#                         result_text += f"\n\n{snippet}"
#                     response_lines.append(result_text)

#                 response = "Web search results:\n\n" + "\n\n".join(response_lines)
#                 response = strip_thinking(response)

#                 step = trace.add("generate", "Generated web search response", response_type="web")
#                 yield {"type": "step", "step": step}

#                 add_new_message(session_id, "user", user_query)
#                 add_new_message(session_id, "assistant", response)
#                 yield {"type": "done", "response": response, "file": None, "rag_used": trace.rag_used}
#                 return
#             else:
#                 print("Web search returned no results; continuing with normal response flow.")

#     print("\n[Step 4] Orchestrating...")
#     rag_needed = needs_rag(rewritten_query, conversation_history)
#     step = trace.add(
#         "orchestrate",
#         "Decided whether retrieval is needed",
#         rag_needed=rag_needed
#     )
#     yield {"type": "step", "step": step}

#     if not rag_needed:
#         print("\n[Step 5] Generating direct response...")
#         response = generate_direct_response(user_query, conversation_history)

#         response = strip_thinking(response)
#         if not response.strip():
#             response = "I was unable to generate a response. Please try again."

#         step = trace.add("generate", "Generated direct response", response_type="direct")
#         yield {"type": "step", "step": step}

#         if file_request["generate"]:
#             file_info, file_step = _generate_requested_file(user_query, file_request, response, trace)
#             file_generated = True
#             yield {"type": "step", "step": file_step}

#         add_new_message(session_id, "user", user_query)
#         add_new_message(session_id, "assistant", response)
#         print("\n" + "="*60)
#         print(f"RESPONSE: {response}")
#         print("="*60)
#         yield {
#             "type": "done",
#             "response": response,
#             "file": file_info if file_generated else None,
#             "rag_used": trace.rag_used,
#         }
#         return

#     print("\n[Step 4] RAG path selected. Starting retrieval...")
#     trace.rag_used = True

#     current_rewritten_query = rewritten_query
#     retry_count = 0

#     while retry_count <= MAX_RETRIES:

#         print(f"\n[Step 5] Retrieving documents (attempt {retry_count + 1}/{MAX_RETRIES + 1})...")
#         ranked_docs = retrieve_with_scores(vectorstore, current_rewritten_query, project_id=project_id)
#         retrieved_docs = [item["doc"] for item in ranked_docs]
#         step = trace.add(
#             "retrieval",
#             f"Retrieved documents (attempt {retry_count + 1})",
#             attempt=retry_count + 1,
#             query=current_rewritten_query,
#             documents=_serialize_ranked_docs(ranked_docs)
#         )
#         yield {"type": "step", "step": step}

#         print(f"\n[Step 6] Evaluating retrieved documents...")
#         evaluation = evaluate_documents(
#             original_query=user_query,
#             rewritten_query=current_rewritten_query,
            
#             retrieved_docs=retrieved_docs
#         )
#         step = trace.add(
#             "evaluation",
#             f"Evaluated relevance (attempt {retry_count + 1})",
#             attempt=retry_count + 1,
#             relevant=evaluation["relevant"],
#             feedback=evaluation.get("feedback")
#         )
#         yield {"type": "step", "step": step}

#         if evaluation["relevant"]:
#             print("\n[Step 7] Documents are relevant. Generating grounded response...")
            
#             response = generate_grounded_response(
#                 original_query=user_query,
#                 rewritten_query=current_rewritten_query,
#                 retrieved_docs=retrieved_docs,
#                 conversation_history=conversation_history
#             )
#             response = strip_thinking(response)
#             if not response.strip():
#                 response = "I was unable to generate a response. Please try again."

#             if file_request["generate"]:

#                 file_info, file_step = _generate_requested_file(user_query, file_request, response, trace)
#                 file_generated = True

#                 yield {
#                     "type": "step",
#                     "step": file_step
#                 }

#                 add_new_message(session_id, "user", user_query)
#                 add_new_message(session_id, "assistant", response)

#                 yield {
#                     "type": "done",
#                     "response": response,
#                     "file": file_info,
#                     "rag_used": trace.rag_used
#                 }

#                 return

#             step = trace.add("generate", "Generated grounded response", response_type="grounded", attempt=retry_count + 1)
#             yield {"type": "step", "step": step}

#             add_new_message(session_id, "user", user_query)
#             add_new_message(session_id, "assistant", response)

#             print("\n" + "="*60)
#             print(f"RESPONSE: {response}")
#             print("="*60)
#             yield {"type": "done", "response": response, "file": None, "rag_used": trace.rag_used}
#             return

#         print(f"\n[Step 8] Documents not relevant.")
#         print(f"  Retries used: {retry_count}/{MAX_RETRIES}")

#         if retry_count >= MAX_RETRIES:
#             print("\n[Step 9] Retry limit reached. Returning safe response...")
#             response = generate_safe_response(user_query)
#             response = strip_thinking(response)
#             if not response.strip():
#                 response = "I was unable to generate a response. Please try again."

#             step = trace.add(
#                 "fallback",
#                 "Retry limit reached — returned a safe fallback response",
#                 response_type="safe",
#                 reason="retry_limit_reached",
#                 retries_used=retry_count
#             )
#             yield {"type": "step", "step": step}

#             add_new_message(session_id, "user", user_query)
#             add_new_message(session_id, "assistant", response)
#             print("\n" + "="*60)
#             print(f"RESPONSE: {response}")
#             print("="*60)
#             yield {"type": "done", "response": response, "file": None, "rag_used": trace.rag_used}
#             return

#         retry_count += 1
#         print(f"\n[Step 9] Retrying with improved query (attempt {retry_count})...")

#         current_rewritten_query = rewrite_query_with_feedback(
#             original_query=user_query,
#             rewritten_query=current_rewritten_query,
#             feedback=evaluation["feedback"],
#             conversation_history=conversation_history
#         )
#         step = trace.add(
#             "refine",
#             f"Refined query using evaluator feedback (attempt {retry_count})",
#             attempt=retry_count,
#             new_query=current_rewritten_query
#         )
#         yield {"type": "step", "step": step}

#     response = generate_safe_response(user_query)
#     response = strip_thinking(response)
#     if not response.strip():
#         response = "I was unable to generate a response. Please try again."

#     step = trace.add(
#         "fallback",
#         "Returned a safe fallback response",
#         response_type="safe",
#         reason="loop_exit"
#     )
#     yield {"type": "step", "step": step}

#     add_new_message(session_id, "user", user_query)
#     add_new_message(session_id, "assistant", response)

#     yield {"type": "done", "response": response, "file": None, "rag_used": trace.rag_used}


def run_pipeline_stream(user_query, session_id="default", use_web_search=False):
    """
    Public generator entry point for callers (e.g. a Flask SSE route) that want
    each trace step the moment it happens. Yields the same events as
    _run_pipeline_steps — see that function's docstring for the event shapes.
    """
    yield from _run_pipeline_steps(user_query, session_id, use_web_search=use_web_search)


def run_pipeline(user_query, session_id="default", use_web_search=False):
    """
    Synchronous wrapper kept for backward compatibility (CLI usage below, and
    any other caller that just wants the final (response, file, trace_dict)
    tuple without streaming). Internally drains the streaming generator.

    NOTE: this now forwards `use_web_search` to the generator, which the
    previous version of this function silently dropped — that was a bug
    (run_pipeline("...", use_web_search=True) never actually triggered a
    web search). Fixed here since it's the same code path we're editing.
    """
    steps = []
    response = None
    file_info = None
    rag_used = False

    for event in _run_pipeline_steps(user_query, session_id, use_web_search=use_web_search):
        if event["type"] == "step":
            steps.append(event["step"])
        elif event["type"] == "done":
            response = event["response"]
            file_info = event.get("file")
            rag_used = event["rag_used"]

    return response, {"steps": steps, "rag_used": rag_used, "file": file_info}



def chat(username):
    print("\n" + "="*60)
    print("RAG Pipeline ready. Type 'quit' to exit.")
    print("Type 'clear' to clear conversation history.")
    print("="*60 + "\n")
    user_id = get_or_create_user_id(username)
    print(user_id)
    session_id = create_conversation(user_id)
    while True:
        user_input = input("You: ").strip()
        
        if not user_input:
            continue
        
        if user_input.lower() == "quit":
            print("Goodbye.")
            break
        
        if user_input.lower() == "clear":
            from db import delete_conversation
            delete_conversation(session_id)
            print("Conversation history cleared.\n")
            continue
        
        response, trace = run_pipeline(user_input, session_id)
        print(f"\nAssistant: {response}\n")
        if trace.get("file"):
            print(f"[Saved file: {trace['file']['path']}]\n")



if __name__ == "__main__":
    chat("mehreen")