# Agentic RAG Chat — Architecture Documentation

A complete walkthrough of how this codebase works, from browser click to vector search to streamed reply.

---

## Table of Contents

1. [High-level overview](#1--high-level-overview)
2. [Authentication & sessions](#2--authentication--sessions)
3. [Database schema & persistence](#3--database-schema--persistence)
4. [The RAG pipeline (pipeline.py)](#4--the-rag-pipeline-pipelinepy)
5. [Document upload & ingestion pipeline](#5--document-upload--ingestion-pipeline)
6. [Retrieval & scoping](#6--retrieval--scoping)
7. [Streaming architecture (SSE)](#7--streaming-architecture-sse)
8. [Frontend state & rendering (app.js)](#8--frontend-state--rendering-appjs)
9. [File generation (text → document)](#9--file-generation-text--document)
10. [Known gaps / things to watch out for](#10--known-gaps--things-to-watch-out-for)
11. [End-to-end summary: "I send a message right now"](#11--end-to-end-summary-i-send-a-message-right-now)

---

## 1 — High-level overview

### What is this project?

This is a **RAG (Retrieval-Augmented Generation) chatbot web app**. Users log in, create conversations (optionally grouped into **projects**), upload documents (PDF, DOCX, TXT, audio/video), and ask questions. The backend decides whether to answer from general LLM knowledge or to **retrieve relevant document chunks** from a vector store, then streams the answer back to the browser in real time along with a **pipeline trace** showing each step (rewrite, retrieval, evaluation, generation, etc.).

### Major moving pieces

| Piece | Technology | Role |
|-------|------------|------|
| **Web server** | Flask (`app.py`) | HTTP routes, SSE streaming, file upload/download |
| **Frontend** | Vanilla JS (`static/js/app.js`) + HTML/CSS | Chat UI, sidebar, trace panel, auth forms |
| **Auth** | Flask-JWT-Extended + bcrypt (`auth.py`) | JWT in httpOnly cookies, CSRF, refresh rotation |
| **Primary DB** | PostgreSQL (`db.py`) | Users, conversations, messages, projects, document metadata |
| **Vector store** | ChromaDB (`retrieval.py`, `ingestion.py`) | Embedded document chunks for similarity search |
| **Graph store** | Neo4j (`graph_store.py`) | Document/chunk nodes (parallel to Postgres metadata) |
| **LLM calls** | Groq API via `llm_client.py` | Query rewrite, orchestration, evaluation, generation |
| **Web search** | Tavily API (`web_search_mcp.py`) | Optional live web results when user toggles web search |
| **File generation** | `tools/file_generator.py` | PDF/DOCX/TXT from assistant responses |

Docker Compose (`dockercompose.yaml`) runs the Flask app alongside **Postgres** and **Neo4j**.

### Message flow (browser → pipeline → browser)

```
┌─────────────┐     POST /api/chat (JSON + cookies)      ┌──────────────┐
│  Browser    │ ───────────────────────────────────────► │  app.py      │
│  app.js     │                                          │  @jwt_required│
└─────────────┘                                          └──────┬───────┘
       ▲                                                          │
       │ SSE: data: {"type":"step",...}                          │ run_pipeline_stream()
       │      data: {"type":"done",...}                           ▼
       │                                                   ┌──────────────┐
       └───────────────────────────────────────────────────│  pipeline.py │
                                                            │  _run_pipeline│
                                                            │  _steps()     │
                                                            └──────┬───────┘
                                                                   │
                    ┌──────────────────────────────────────────────┼──────────────────────────┐
                    ▼              ▼              ▼                ▼              ▼           ▼
              db.py (history)  query_rewriter  orchestrator   retrieval.py   evaluator   response_generator
                    │              │              │                │              │           │
                    │              └──────────────┴────────────────┴──────────────┴───────────┘
                    │                                    ChromaDB (vectorstore)
                    ▼
              add_new_message (user + assistant rows saved at end)
```

**Plain-English flow:**

1. User types a message and submits the composer form.
2. `app.js` POSTs to `/api/chat` with `{ message, session_id, web_search }`.
3. Flask verifies the JWT cookie, confirms the conversation belongs to the user.
4. `run_pipeline_stream()` runs step-by-step, **yielding** trace events as each completes.
5. Flask wraps each event as an SSE frame: `data: {...}\n\n`.
6. The frontend reads the stream, appends live trace steps, then finalizes the assistant bubble on `done`.
7. Messages are persisted to Postgres inside `_finish()` before the final `done` event is emitted.

---

## 2 — Authentication & sessions

### Design overview

Authentication uses **JWT access + refresh tokens stored in httpOnly cookies** (not localStorage). The frontend sends cookies automatically via `credentials: 'include'` and echoes a **CSRF token** from a companion cookie on state-changing requests.

From `auth.py`:

```python
"""
Authentication blueprint.

Design:
- Passwords hashed with bcrypt, never stored/logged in plaintext.
- JWT access token (short-lived, 30 min) + refresh token (long-lived, 30 days),
  both delivered as httpOnly cookies so the existing frontend doesn't need to
  manage Authorization headers manually.
- CSRF protection is enabled for the cookie flow (flask-jwt-extended sets a
  companion non-httpOnly CSRF cookie; the frontend must echo its value back
  in an X-CSRF-TOKEN header on state-changing requests).
- Refresh tokens are tracked server-side by jti in the `refresh_tokens` table,
  which is what makes logout / logout-all real: we revoke by row, not by
  waiting for a stateless token to expire.
"""
```

JWT configuration in `app.py`:

```python
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=30)
app.config["JWT_ACCESS_COOKIE_PATH"] = "/api/"
app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth/refresh"
app.config["JWT_COOKIE_CSRF_PROTECT"] = True
app.config["JWT_BLOCKLIST_ENABLED"] = True
app.config["JWT_BLOCKLIST_TOKEN_CHECKS"] = ["refresh"]
```

### Access token vs refresh token

| Token | Lifetime | Cookie path | Purpose |
|-------|----------|-------------|---------|
| **Access** | 30 minutes | `/api/` | Authenticates every API call (`@jwt_required()`) |
| **Refresh** | 30 days | `/api/auth/refresh` | Used only at `POST /api/auth/refresh` to get new tokens |

**When access expires:** The next API call returns 401. The frontend would need to call `/api/auth/refresh` (not currently wired in `app.js` on 401 — see Section 10). Refresh tokens are **rotated** on each refresh: the old `jti` is revoked and a new pair is issued.

Blocklist check (refresh only):

```python
@jwt.token_in_blocklist_loader
def check_if_revoked(jwt_header, jwt_payload):
    if jwt_payload.get("type") != "refresh":
        return False
    return db.is_refresh_token_revoked(jwt_payload["jti"])
```

### Registration flow

`POST /api/auth/register` — validates username (≥3 chars) and password (≥8 chars), bcrypt-hashes the password, inserts into `users`:

```python
@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower() or None
    password = data.get("password") or ""

    if len(username) < 3:
        return jsonify({"error": "Username must be at least 3 characters."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    try:
        user_id = db.create_user(username, pw_hash, email)
    except psycopg2.errors.UniqueViolation:
        return jsonify({"error": "Username or email already in use."}), 409

    return jsonify({"message": "Account created.", "user_id": user_id}), 201
```

The frontend registers, then switches to login mode — it does **not** auto-login after register.

### Login flow

`POST /api/auth/login` — accepts email or username + password, verifies bcrypt hash, issues tokens, sets cookies:

```python
@auth_bp.route("/login", methods=["POST"])
def login():
    identifier = (data.get("email") or data.get("username") or "").strip()
    password = data.get("password") or ""

    if "@" in identifier:
        user = db.get_user_by_email(identifier)
    else:
        user = db.get_user_by_username(identifier)

    if not user or not bcrypt.check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid email or password."}), 401

    access_token, refresh_token = _issue_tokens(user["id"])

    resp = jsonify({"username": user["username"], "user_id": user["id"]})
    set_access_cookies(resp, access_token)
    set_refresh_cookies(resp, refresh_token)
    return resp, 200
```

Token issuance stores the refresh token's `jti` in Postgres:

```python
def _issue_tokens(user_id):
    access_token = create_access_token(identity=str(user_id))
    refresh_token = create_refresh_token(identity=str(user_id))

    decoded = decode_token(refresh_token)
    expires_at = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)

    db.store_refresh_token(
        jti=decoded["jti"],
        user_id=user_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip,
    )
    return access_token, refresh_token
```

### Frontend auth wiring

On page load, the app probes session state:

```javascript
authFetch("/api/me")
  .then(r => r.json())
  .then(data => {
    if (data.logged_in) {
      showApp(data.username, data.conversations).catch(() => showLogin());
    } else {
      showLogin();
    }
  })
  .catch(() => showLogin());
```

CSRF header on every mutating request:

```javascript
function getCsrfToken() {
  return document.cookie
    .split('; ')
    .find(row => row.startsWith('csrf_access_token='))
    ?.split('=')[1] || '';
}

function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const csrf = getCsrfToken();
  if (csrf) headers.set('X-CSRF-TOKEN', csrf);
  return fetch(url, {
    ...options,
    credentials: 'include',
    headers,
  });
}
```

### Protecting API routes

Every protected route uses `@jwt_required()` and reads the user id:

```python
@app.route("/api/chat", methods=["POST"])
@jwt_required()
def chat():
    user_id = int(get_jwt_identity())
    # ...
    if not session_id or not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404
```

Ownership check in `db.py`:

```python
def conversation_belongs_to_user(user_id, conversation_id):
    cur.execute(
        """
        SELECT 1 FROM conversations
        WHERE id = %s AND user_id = %s
        """,
        (conversation_id, user_id)
    )
    return cur.fetchone() is not None
```

---

## 3 — Database schema & persistence

### Tables

The repo defines schema creation in `db.py` via idempotent `ensure_*` functions. Core chat tables (`users`, `conversations`, `messages`) are **assumed to pre-exist** (Docker Compose references `./migrations.sql` at Postgres init, but that file is not present in the repo snapshot — see Section 10). Their shape is inferred from queries:

#### `users`
| Column | Meaning |
|--------|---------|
| `id` | Primary key |
| `username` | Unique login name |
| `password_hash` | bcrypt hash (added via `ALTER TABLE`) |
| `email` | Optional, unique when set |
| `is_active` | Account enabled flag |
| `created_at` | Registration timestamp |

Auth extensions created by `ensure_auth_tables()`:

```python
cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
```

#### `conversations`
| Column | Meaning |
|--------|---------|
| `id` | Session ID (used as `session_id` in API) |
| `user_id` | Owner FK → `users.id` |
| `title` | Sidebar label (auto-set from first message) |
| `created_at` | Creation time |
| `project_id` | Optional FK → `projects.id` (`NULL` = general chat) |

#### `messages`
| Column | Meaning |
|--------|---------|
| `id` | Primary key |
| `conversation_id` | FK → `conversations.id` |
| `role` | `"user"` or `"assistant"` |
| `content` | Message text |
| `created_at` | Ordering for history load |

#### `projects`
Created by `ensure_project_tables()`:

```python
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

#### `refresh_tokens` & `auth_events`
Server-side session tracking and audit log (see Section 2).

#### `documents`, `document_chunks`, `document_references`
Document metadata and chunk text in Postgres; embeddings live in Chroma:

```python
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    neo4j_id TEXT,
    title TEXT,
    filename TEXT,
    file_path TEXT,
    mime_type TEXT,
    storage_type TEXT,
    source_url TEXT,
    content_hash TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    uploaded_by_user_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
```

(`project_id` is passed in one overload of `register_document` — see Section 10 for a scoping bug.)

### Creating a conversation

General chat:

```python
@app.route("/api/conversations", methods=["POST"])
@jwt_required()
def new_conversation():
    user_id = int(get_jwt_identity())
    session_id = create_conversation(user_id)
    return jsonify({"session_id": str(session_id), "title": "New chat"})
```

Project-scoped chat:

```python
session_id = db.create_conversation(user_id, title="New chat", project_id=project_id)
```

Implementation:

```python
def create_conversation(user_id, title="New Chat", project_id=None):
    cur.execute(
        "INSERT INTO conversations (user_id, title, project_id) VALUES (%s, %s, %s) RETURNING id",
        (user_id, title, project_id),
    )
    return cur.fetchone()[0]
```

### Saving messages

Called once at pipeline completion in `_finish()`:

```python
def add_new_message(conversation_id, role, content):
    cur.execute(
        """
        INSERT INTO messages
        (conversation_id, role, content)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (conversation_id, role, content),
    )
    return cur.fetchone()[0]
```

Both user query and assistant response are saved together, **after** generation completes — not incrementally during streaming.

### Loading conversation history

Used at pipeline start and when switching chats:

```python
def load_conversation_history(conversation_id):
    cur.execute(
        """
        SELECT role, content
        FROM messages
        WHERE conversation_id = %s
        ORDER BY created_at
        """,
        (conversation_id,),
    )
    return cur.fetchall()
```

API exposure:

```python
@app.route("/api/conversations/<session_id>/messages")
@jwt_required()
def conversation_messages(session_id):
    history = load_conversation_history(session_id)
    return jsonify({"messages": _serialize_history(history)})
```

Note: **trace data is not persisted** — only `role` and `content` are stored. Reopening a past chat shows message text but no pipeline trace unless it was captured client-side (it isn't saved to DB).

### Project scoping relationships

```
users
  └── projects (user_id)
        └── conversations (project_id) — chats inside a project
        └── documents (project_id in metadata + Chroma filter)

users
  └── conversations (project_id IS NULL) — "general" chats
        └── uploads → documents scoped as project_id="general" in Chroma
```

Listing by scope:

```python
def load_conversations_by_scope(user_id, project_id=None):
    """project_id=None -> general chats (project_id IS NULL).
    project_id=<id> -> chats inside that project."""
    if project_id is None:
        cur.execute(
            "SELECT id, title, created_at FROM conversations "
            "WHERE user_id = %s AND project_id IS NULL ORDER BY created_at DESC",
            (user_id,),
        )
    else:
        cur.execute(
            "SELECT id, title, created_at FROM conversations "
            "WHERE user_id = %s AND project_id = %s ORDER BY created_at DESC",
            (user_id, project_id),
        )
```

---

## 4 — The RAG pipeline (pipeline.py)

### Entry points

**Streaming (web UI):**

```python
def run_pipeline_stream(user_query, session_id="default", use_web_search=False):
    yield from _run_pipeline_steps(user_query, session_id, use_web_search=use_web_search)
```

**Synchronous (CLI):**

```python
def run_pipeline(user_query, session_id="default", use_web_search=False):
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
```

Both drain the same generator — the only difference is whether events are consumed incrementally or batched.

### PipelineTrace

Collects structured step records for the SSE stream and UI:

```python
class PipelineTrace:
    def __init__(self):
        self.steps = []
        self.rag_used = False

    def add(self, step_type, label, **data):
        self.steps.append({"type": step_type, "label": label, **data})
        return self.steps[-1]

    def to_dict(self):
        return {"steps": self.steps, "rag_used": self.rag_used}
```

Each `trace.add(...)` immediately yields a `{"type": "step", "step": {...}}` event to the client.

### `_finish()` — single exit point

Every branch that produces a final answer routes through `_finish()`:

```python
def _finish(user_query, file_request, response, trace, session_id):
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
    })
    return events
```

This ensures file generation and DB writes happen in one place regardless of which branch (web search, direct, grounded, fallback) produced the response.

### Step-by-step execution of `_run_pipeline_steps()`

#### Step 0 — File intent detection

```python
file_request = detect_file_request(user_query)
```

Checks for keywords like "pdf", "docx", "generate file" (see Section 9). Runs once upfront; file is generated at the end if flagged.

#### Step 1 — Load conversation history

```python
conversation_history = load_conversation_history(session_id)
step = trace.add("history", "Loaded conversation history", message_count=len(conversation_history or []))
yield {"type": "step", "step": step}
project_id = get_conversation_project_id(session_id)
```

**Why:** Later stages (query rewrite, direct response) need prior turns for context. `project_id` scopes retrieval to the conversation's project.

#### Step 2 — Query rewriting

```python
rewritten_query = rewrite_query(user_query, conversation_history)
step = trace.add("rewrite", "Rewrote query",
    original_query=user_query, rewritten_query=rewritten_query)
yield {"type": "step", "step": step}
```

**Why before retrieval:** User messages are often ambiguous ("what about the first step?"). The rewriter resolves pronouns and produces a self-contained search query optimized for vector similarity. Implemented in `query_rewriter.py` via an LLM prompt.

#### Step 3 — Optional web search (user-toggled)

If `use_web_search=True` (from the 🌐 toggle in the UI):

```python
if use_web_search:
    web_search = get_web_search_tool()
    if web_search.is_available():
        results = web_search.search(user_query, top_k=3)
        # ... format results as markdown links ...
        for evt in _finish(user_query, file_request, response, trace, session_id):
            yield evt
        return
```

Uses Tavily API. If results exist, the pipeline **short-circuits** — no RAG retrieval. If unavailable or empty, continues to orchestration.

#### Step 4 — Orchestration (RAG needed?)

```python
rag_needed = needs_rag(rewritten_query, conversation_history)
step = trace.add("orchestrate", "Decided whether retrieval is needed", rag_needed=rag_needed)
yield {"type": "step", "step": step}
```

`orchestrator.py` uses keyword regexes first (greetings → direct, "summarize document" → RAG), then an LLM fallback for ambiguous queries. Returns `False` for casual chat, `True` for document-style questions.

**Direct path (no RAG):**

```python
if not rag_needed:
    response = generate_direct_response(user_query, conversation_history)
    for evt in _finish(user_query, file_request, response, trace, session_id):
        yield evt
    return
```

#### Step 5–9 — RAG path with retry loop

```python
trace.rag_used = True
current_rewritten_query = rewritten_query
retry_count = 0

while retry_count <= MAX_RETRIES:  # MAX_RETRIES = 3
```

**Step 5 — Retrieval:**

```python
ranked_docs = retrieve_with_scores(vectorstore, current_rewritten_query, project_id=project_id)
step = trace.add("retrieval", f"Retrieved documents (attempt {retry_count + 1})",
    attempt=retry_count + 1, query=current_rewritten_query,
    documents=_serialize_ranked_docs(ranked_docs))
yield {"type": "step", "step": step}
```

**Step 6 — Evaluation:**

```python
evaluation = evaluate_documents(
    original_query=user_query,
    rewritten_query=current_rewritten_query,
    retrieved_docs=retrieved_docs
)
step = trace.add("evaluation", f"Evaluated relevance (attempt {retry_count + 1})",
    relevant=evaluation["relevant"], feedback=evaluation.get("feedback"))
yield {"type": "step", "step": step}
```

The evaluator is an LLM that returns JSON `{"relevant": true/false, "feedback": "..."}`. It checks whether retrieved chunks are on-topic enough to answer the question.

**Why the retry loop:** If documents aren't relevant, the pipeline rewrites the query using evaluator feedback and tries retrieval again — up to 3 retries:

```python
if retry_count >= MAX_RETRIES:
    response = generate_safe_response(user_query)
    # ... fallback ...
    return

retry_count += 1
current_rewritten_query = rewrite_query_with_feedback(
    original_query=user_query,
    rewritten_query=current_rewritten_query,
    feedback=evaluation["feedback"],
    conversation_history=conversation_history
)
```

**Step 7 — Grounded generation (when relevant):**

```python
if evaluation["relevant"]:
    response = generate_grounded_response(
        original_query=user_query,
        rewritten_query=current_rewritten_query,
        retrieved_docs=retrieved_docs,
        conversation_history=conversation_history
    )
    for evt in _finish(user_query, file_request, response, trace, session_id):
        yield evt
    return
```

Documents are injected into the LLM prompt with `[Document N | Source: ... | Page: ...]` headers. The model is instructed to cite sources and not invent facts.

### Trace events → UI

The same step dicts flow to:
1. **SSE stream** → `appendLiveTraceStep()` (right-side Pipeline Trace panel)
2. **Per-message reasoning** → `appendStreamingReasoningStep()` (collapsible "Show thinking" under the assistant bubble)
3. **Trace pill** → `attachTracePill()` stores trace in `traceStore` Map for later inspection

---

## 5 — Document upload & ingestion pipeline

### 1. Frontend — file selection and upload

User opens the + menu → "Upload document" → hidden file input. Upload uses **XHR + FormData** for progress tracking (not `fetch`):

```javascript
function uploadFileWithProgress(file) {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    formData.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/conversations/${activeSessionId}/upload`);

    const csrf = getCsrfToken();
    if (csrf) xhr.setRequestHeader("X-CSRF-TOKEN", csrf);
    xhr.withCredentials = true;

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && uploadingFile) {
        uploadingFile.percent = Math.round((e.loaded / e.total) * 100);
        renderUploadProgress();
      }
    });
    // ...
    xhr.send(formData);
  });
}
```

Progress appears in a floating badge above the composer (`#upload-progress`), positioned absolutely so it doesn't affect textarea width.

### 2. Backend route — validation and disk storage

```python
@app.route("/api/conversations/<session_id>/upload", methods=["POST"])
@jwt_required()
def upload_document(session_id):
    user_id = int(get_jwt_identity())
    if not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    project_id = db.get_conversation_project_id(session_id)
    dest_dir = os.path.join(UPLOAD_DIR, str(project_id) if project_id else "general")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    file.save(dest_path)

    document_id = ingest_uploaded_file(
        dest_path, filename,
        project_id=project_id,
        uploaded_by_user_id=user_id,
        vectorstore=vectorstore,
    )
    return jsonify({"document_id": document_id, "filename": filename, "project_id": project_id})
```

Files land under `uploads/<project_id>/` or `uploads/general/`.

### 3. ingestion.py — text extraction, chunking, embedding

Entry point:

```python
def ingest_uploaded_file(file_path, filename, project_id=None, uploaded_by_user_id=None, vectorstore=None):
    if filename.endswith(".pdf"):
        loaded = load_pdf_smart(file_path)
    elif filename.endswith(".docx"):
        loaded = Docx2txtLoader(file_path).load()
    elif filename.endswith(".txt"):
        loaded = TextLoader(file_path).load()
    elif filename.endswith((".mp4", ".mp3", ".wav")):
        loaded = transcribe_audio(file_path, filename)
    else:
        raise ValueError(f"Unsupported file type: {filename}")

    for doc in loaded:
        doc.metadata.update(scope_metadata(project_id))

    document_id = persist_document_to_db_and_graph(...)

    chunks = chunk_docs(loaded)
    vectorstore.add_documents(chunks)
    persist_document_chunks(document_id, chunks)

    return document_id
```

**Libraries used:**
- **PDF:** PyMuPDF (`fitz`) — text extraction per page; pages with large images get a placeholder (vision OCR is stubbed)
- **DOCX:** LangChain `Docx2txtLoader`
- **TXT:** LangChain `TextLoader`
- **Audio/video:** OpenAI Whisper (`whisper.load_model("base")`) → transcript segments chunked ~1000 chars

**Chunking:**

```python
def chunk_docs(documents):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        length_function=len
    )
    return splitter.split_documents(documents)
```

**Embedding model:**

```python
def get_embedding_model():
    embeddings = HuggingFaceEmbeddings(
        model_name="BAAI/bge-base-en-v1.5",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 64}
    )
    return embeddings
```

Chunks are written to **ChromaDB** at `./chroma_db` via `vectorstore.add_documents(chunks)`.

### 4. Project scoping during ingestion

Every chunk gets metadata tags:

```python
def scope_metadata(project_id=None):
    return {
        "project_id": project_id if project_id else "general",
        "scope": "project" if project_id else "general",
    }
```

Retrieval filters on `project_id` (see Section 6). A document uploaded in a project-scoped conversation is only searchable from chats in that same project.

### 5. Postgres vs Chroma vs Neo4j

| Store | What's stored |
|-------|---------------|
| **Postgres `documents`** | File metadata (path, filename, uploader, title) |
| **Postgres `document_chunks`** | Chunk text + page numbers (for `/docs/<id>` and citations) |
| **ChromaDB** | Chunk embeddings + metadata (used for similarity search) |
| **Neo4j** | Document and chunk nodes (graph relationships — parallel audit trail) |

Chunk rows in Postgres:

```python
def register_document_chunk(document_id, chunk_index, page_number, text, metadata=None):
    cur.execute(
        "INSERT INTO document_chunks (document_id, chunk_index, page_number, text, metadata) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (document_id, chunk_index, page_number, text, json.dumps(metadata)),
    )
```

---

## 6 — Retrieval & scoping

### `retrieve_with_scores()`

```python
def retrieve_with_scores(vectorstore, query, top_k=10, final_k=5, project_id=None):
    return _retrieve_ranked(vectorstore, query, top_k=top_k, final_k=final_k, project_id=project_id)
```

Core retrieval in `_retrieve_ranked()`:

```python
def _retrieve_ranked(vectorstore, query, top_k=10, final_k=5, project_id=None):
    where_filter = {"project_id": project_id if project_id else "general"}

    semantic_results = vectorstore.similarity_search_with_score(
        query=query, k=top_k, filter=where_filter
    )

    keywords = " ".join([word for word in query.split() if len(word) > 3])
    keyword_results = vectorstore.similarity_search_with_score(
        query=keywords, k=top_k, filter=where_filter
    )

    reranked = reciprocal_rank_fusion([semantic_results, keyword_results])
    top_results = reranked[:final_k]
    return top_results
```

**How it works:**
1. **Embedding model:** `BAAI/bge-base-en-v1.5` (same as ingestion)
2. **Search method:** Chroma `similarity_search_with_score` (cosine distance on normalized embeddings)
3. **Dual search:** Full query + keyword-stripped query
4. **Fusion:** Reciprocal Rank Fusion (RRF) merges both ranked lists
5. **Scoping:** `filter={"project_id": "<id>"}` or `"general"` — a project chat never sees general docs and vice versa

RRF formula:

```python
scores[doc_key]["score"] += 1 / ((rank + 1) + k)  # k=60
```

### Serialization for trace display

Before steps are sent to the UI, documents are trimmed for the trace panel:

```python
def _serialize_ranked_docs(ranked_docs):
    serialized = []
    for item in ranked_docs:
        doc = item["doc"]
        content = doc.page_content or ""
        serialized.append({
            "source": doc.metadata.get("source"),
            "page": doc.metadata.get("page"),
            "file_path": doc.metadata.get("file_path"),
            "source_url": doc.metadata.get("source_url"),
            "score": round(float(item["score"]), 4),
            "preview": content[:DOC_PREVIEW_CHARS],  # 220 chars
            "truncated": len(content) > DOC_PREVIEW_CHARS,
        })
    return serialized
```

The frontend renders these in the trace sidebar and the per-message "Referenced documents" section.

---

## 7 — Streaming architecture (SSE)

### Why SSE?

The RAG pipeline takes several seconds and produces **multiple intermediate steps** (rewrite, retrieval, evaluation). A normal request/response would force the user to wait with no feedback until everything finishes. SSE lets the server **push each step as it completes** over a single long-lived HTTP connection — simpler than WebSockets for one-way server→client updates.

### Server side

```python
def event_stream():
    try:
        for event in run_pipeline_stream(user_query, session_id, use_web_search=use_web_search):
            if event["type"] == "step":
                yield sse({"type": "step", "step": event["step"]})
            elif event["type"] == "done":
                yield sse({
                    "type": "done",
                    "response": event["response"],
                    "file": event.get("file"),
                    "rag_used": event["rag_used"],
                    "title": title,
                })
    except Exception as exc:
        yield sse({"type": "error", "error": f"Pipeline error: {exc}"})

return Response(
    stream_with_context(event_stream()),
    mimetype="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

Each frame: `data: {"type":"step","step":{...}}\n\n`

### Client side

```javascript
const res = await authFetch("/api/chat", {
  method: "POST",
  body: JSON.stringify({ message: text, session_id: activeSessionId, web_search: webSearchEnabled })
});

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { value, done } = await reader.read();
  if (done) break;

  buffer += decoder.decode(value, { stream: true });
  const { events, rest } = parseSSEBuffer(buffer);
  buffer = rest;

  for (const evt of events) {
    if (evt.type === "step") {
      appendLiveTraceStep(evt.step);
      appendStreamingReasoningStep(assistantRow.reasoningBody, assistantRow.docsBody, evt.step);
    } else if (evt.type === "done") {
      finalizeAssistantMessageRow(assistantRow.row, assistantRow.body, finalTrace, evt.response, evt.file);
    } else if (evt.type === "error") {
      addError(evt.error);
    }
  }
}
```

SSE frame parser:

```javascript
function parseSSEBuffer(buffer) {
  const frames = buffer.split("\n\n");
  const rest = frames.pop();
  const events = [];
  for (const frame of frames) {
    const line = frame.trim();
    if (!line.startsWith("data:")) continue;
    events.push(JSON.parse(line.slice(5).trim()));
  }
  return { events, rest };
}
```

### Event types

| Event | Payload | Frontend action |
|-------|---------|-----------------|
| `step` | `{ step: { type, label, ...fields } }` | Append to live trace timeline + reasoning panel |
| `done` | `{ response, file?, rag_used, title? }` | Remove typing indicator, render final markdown, collapse reasoning, attach trace pill, update conversation title |
| `error` | `{ error: "..." }` | Show error message, remove typing indicator |

---

## 8 — Frontend state & rendering (app.js)

### Structure

The entire frontend is one **IIFE** (Immediately Invoked Function Expression) in strict mode — no global exports, all state is closure-scoped.

### Key state variables

| Variable | Purpose |
|----------|---------|
| `activeSessionId` | Currently open conversation ID (maps to `conversations.id`) |
| `activeProjectId` | Expanded project in sidebar accordion (`null` = general chats) |
| `webSearchEnabled` | Whether next message uses Tavily web search |
| `uploadingFile` | `{ name, percent }` during file upload |
| `traceStore` | `Map<msgId, traceObject>` — traces for completed messages |
| `liveTraceSteps` | Array of steps for the in-flight request |
| `activeTraceMsgId` | Which message's trace is shown in the side panel |
| `traceSidebarOpen` | Pipeline Trace panel visibility |
| `traceUserPinnedState` | User manually closed/opened trace panel (stops auto-open) |

### Conversations and projects

**General conversations** — flat list under "Conversations":

```javascript
function renderGeneralConversations(conversations) {
  generalConvList.innerHTML = "";
  conversations.forEach(conv => {
    // build .conv-item with click → switchTo(session_id, title)
  });
}
```

**Projects** — accordion expand inline under each project:

```javascript
async function selectProject(projectId, projectName) {
  if (String(activeProjectId) === String(projectId)) {
    await clearProjectSelection();  // collapse
    return;
  }
  activeProjectId = projectId;
  await loadProjects();
  await refreshConversations();  // fills .project-chats under expanded project
}
```

Clicking a project **does not** switch the main chat — only clicking a chat inside the accordion calls `switchTo()`.

**Switching chats:**

```javascript
async function switchTo(sessionId, title) {
  activeSessionId = sessionId;
  headerTitle.textContent = title || "Chat";
  msgContainer.innerHTML = "";
  resetTracePanel();
  await refreshConversations();
  const res = await authFetch("/api/conversations/" + sessionId + "/messages");
  renderHistory(data.messages);
}
```

### Reasoning / trace panel — two views

**Live streaming (during request):**
- `startAssistantMessageRow()` creates an expanded reasoning panel
- Each SSE `step` → `appendStreamingReasoningStep()` adds items in real time
- On `done` → `finalizeAssistantMessageRow()` collapses panel to "Show thinking"

**Static trace (after completion):**
- `attachTracePill()` stores trace in `traceStore`
- Clicking the trace pill or using the 🧪 header toggle calls `selectTrace(msgId)`
- Renders full timeline in the right `#trace-sidebar` via `buildTraceHTML(trace)`

**Live sidebar trace:**

```javascript
function appendLiveTraceStep(step) {
  liveTraceSteps.push(step);
  const timeline = document.getElementById("live-trace-timeline");
  timeline.insertAdjacentHTML("beforeend", DOMPurify.sanitize(renderTraceStep(step)));
}
```

---

## 9 — File generation (text → document)

### `detect_file_request()`

Keyword-based detector in `tools/file_detector.py`:

```python
def detect_file_request(query):
    q = query.lower()
    result = {"generate": False, "file_type": "", "title": "", "description": ""}

    if any(k in q for k in ["pdf", ".pdf"]):
        result["file_type"] = "pdf"
    elif any(k in q for k in ["docx", "doc", "word", "document"]):
        result["file_type"] = "docx"
    elif any(k in q for k in ["txt", "text file", "plain text"]):
        result["file_type"] = "txt"
    elif any(k in q for k in ["excel", "spreadsheet", "xlsx", "xls", "csv"]):
        result["file_type"] = "xlsx"

    if result["file_type"]:
        result["generate"] = True

    return result
```

Note: `xlsx` is detected but **not implemented** in `generate_file()` — will raise at runtime.

### `generate_file()`

```python
def generate_file(file_type, query, content, filename=None, **metadata):
    normalized_type = FILE_TYPE_ALIASES.get((file_type or "").strip().lower())
    # ...
    if normalized_type == "pdf":
        path = create_pdf(content, filename, **metadata)      # tools/pdf_generator.py
    elif normalized_type == "docx":
        path = create_docx(content, filename, **metadata)    # tools/docx_generator.py
    elif normalized_type == "txt":
        path = create_txt(content, filename, title=...)
    # ...

    return {
        "path": path,
        "filename": filename,
        "file_type": normalized_type,
        "extension": EXTENSIONS[normalized_type],
    }
```

Files saved to `generated_files/` with a timestamped slug derived from the query.

### Triggered only in `_finish()`

```python
if file_request["generate"]:
    file_info, file_step = _generate_requested_file(user_query, file_request, response, trace)
    events.append({"type": "step", "step": file_step})
```

The LLM response text becomes the document body — file generation happens **after** the answer is written, regardless of whether the path was direct, grounded, or fallback.

### Download route and frontend link

Backend:

```python
@app.route("/download/generated/<path:filename>")
def download_generated_file(filename):
    safe_filename = os.path.basename(filename)
    path = os.path.join(generated_dir, safe_filename)
    return send_file(path, as_attachment=True, download_name=safe_filename)
```

Frontend (in `finalizeAssistantMessageRow`):

```javascript
if (file) {
  const filename = `${file.filename || "generated_document"}${file.extension || ""}`;
  downloadLink.href = `/download/generated/${encodeURIComponent(filename)}`;
  downloadLink.textContent = `Download ${filename}`;
}
```

---

## 10 — Known gaps / things to watch out for

These are observations from reading the code — **not fixed**, just flagged.

1. **`migrations.sql` missing from repo** — `dockercompose.yaml` mounts it for Postgres init, but the file isn't in the workspace. Fresh Docker setups may fail unless tables are created manually.

2. **`db.py` password bug** — `password=int(os.getenv("DB_PASSWORD"))` coerces the DB password to an integer. Non-numeric passwords will crash at connection time.

3. **Duplicate functions in `db.py`** — `register_document()` and `get_conversation_project_id()` each appear twice (one version supports `project_id`, one doesn't). Python uses the last definition, which can silently drop `project_id` from document rows.

4. **`persist_document_to_db_and_graph()` doesn't pass `project_id`** — calls `register_document(...)` without `project_id=project_id` even though `ingest_uploaded_file` receives it. Chroma metadata gets scoped correctly, but the Postgres `documents` row may not.

5. **Trace data not persisted** — reopening a conversation loads message text only; pipeline traces from past sessions are lost.

6. **No automatic token refresh in frontend** — if the 30-minute access token expires mid-session, API calls fail until manual re-login. `/api/auth/refresh` exists but isn't called on 401.

7. **Duplicate `load_vectorstore()` in `retrieval.py`** — defined twice (lines 22 and 102); harmless but confusing.

8. **Two vectorstore instances** — `app.py` loads one at startup; `pipeline.py` loads another at import. They point to the same `./chroma_db` directory but are separate Python objects.

9. **Vision PDF pages are stubbed** — pages with large images get placeholder text, not OCR.

10. **`detect_file_request` detects xlsx but `generate_file` doesn't support it** — will error if user asks for a spreadsheet.

11. **Large commented-out blocks** — `pipeline.py` (~300 lines) and `response_generator.py` contain old implementations that make navigation harder.

12. **Web search bypasses RAG entirely** — when enabled and results exist, no document retrieval happens even if uploaded docs would be more relevant.

13. **`query_rewriter.py` test block** passes dict-shaped history but production passes `(role, content)` tuples — test code doesn't match runtime shape.

14. **Neo4j required for ingestion** — `persist_document_to_db_and_graph` calls `get_graph_store()` which raises if Neo4j env vars are missing; uploads fail without Neo4j even though Chroma alone could suffice.

15. **History API doesn't return traces** — `appendMsg(role, text, trace)` supports traces but `renderHistory()` only passes `role` and `content`.

---

## 11 — End-to-end summary: "I send a message right now"

Here is everything that happens when you type a question and hit send, start to finish:

1. **Composer submit** (`app.js`) — form handler calls `submitChatMessage(text)`.
2. **Session check** — if no `activeSessionId`, creates a conversation via `POST /api/conversations` or `/api/projects/<id>/conversations`.
3. **UI prep** — user bubble appended, typing indicator shown, empty assistant row created with expanded reasoning panel, live trace sidebar opened.
4. **HTTP request** — `POST /api/chat` with JSON body + JWT cookie + CSRF header. `web_search` flag included if 🌐 toggle is on.
5. **Auth gate** (`app.py`) — `@jwt_required()` validates access token; `get_jwt_identity()` → user id; `conversation_belongs_to_user()` confirms ownership.
6. **SSE stream starts** — Flask returns `text/event-stream` response; `event_stream()` generator begins iterating `run_pipeline_stream()`.
7. **File intent** (`pipeline.py`) — `detect_file_request()` checks if you asked for a PDF/DOCX/etc.
8. **History load** — prior messages fetched from Postgres; trace step `history` yielded via SSE → appears in trace panel.
9. **Project scope** — `get_conversation_project_id(session_id)` determines which document pool to search.
10. **Query rewrite** — LLM rewrites your message into a standalone search query; SSE `rewrite` step sent.
11. **Web search branch** (if toggled) — Tavily search may short-circuit with formatted links, skipping RAG entirely.
12. **Orchestration** — keyword/LLM router decides: direct answer or retrieval needed; SSE `orchestrate` step sent.
13. **Direct path** (if no RAG) — LLM generates answer from conversation history only; SSE `generate` step sent → jump to step 18.
14. **Retrieval loop** (RAG path) — Chroma similarity search with project filter + RRF reranking; top 5 chunks returned; SSE `retrieval` step sent with doc previews.
15. **Evaluation** — LLM judges if chunks are relevant; SSE `evaluation` step sent.
16. **Retry or generate** — if not relevant and retries remain, query rewritten with feedback (`refine` step) and loop returns to step 14. If relevant, grounded LLM response generated with document context (`generate` step). If retries exhausted, safe fallback message (`fallback` step).
17. **Optional file generation** — if step 7 detected file intent, PDF/DOCX/TXT built from the response text; SSE `file` step sent.
18. **Persistence** — `_finish()` saves your message and the assistant reply to Postgres `messages` table.
19. **Done event** — SSE `done` with full response text, optional file info, `rag_used` flag, and updated conversation title (if first message).
20. **Frontend finalize** — typing indicator removed, assistant markdown rendered, reasoning panel collapsed to "Show thinking", download link added if file present, trace stored in `traceStore`, sidebar trace updated, conversation list refreshed if title changed.

That is the complete path from keystroke to stored, streamed, traceable answer.
