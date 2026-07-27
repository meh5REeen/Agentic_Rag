# Agentic RAG Chat App

## Project Overview

`Agentic RAG` is a Flask-based retrieval-augmented generation (RAG) chat application that lets authenticated users ask questions over uploaded documents and get grounded answers enriched by document retrieval, query rewriting, evaluation, and tracing.

The system combines:

- a secure web app and authentication layer (`app.py`, `auth.py`, `db.py`)
- a document ingestion pipeline that converts uploaded files into searchable chunks and stores them in a vector database (`ingestion.py`, `retrieval.py`)
- a smart RAG orchestration pipeline that decides when to retrieve knowledge and when to answer directly (`pipeline.py`, `orchestrator.py`)
- an LLM-driven query rewriter, document evaluator, and response generator (`query_rewriter.py`, `evaluator.py`, `response_generator.py`)
- optional web search fallback support for fresh queries (`web_search_mcp.py`)
- a document graph store for metadata and content relationships (`graph_store.py`)

The app exposes an SSE-based chat API so the frontend can stream pipeline trace events while generating answers.

---

## High-Level Architecture

### 1. User and UI Layer

- `static/js/app.js` contains the browser-side chat interface, authentication state, conversation/project navigation, file upload support, and trace sidebar rendering.
- `templates/index.html` and `templates/doc.html` serve the chat UI and document detail pages.
- The frontend sends chat messages to `/api/chat` and uploads documents to `/api/conversations/<session_id>/upload`.

### 2. Web Server and Routing

- `app.py` is the Flask application entrypoint.
- It configures JWT-based authentication using `flask_jwt_extended` and the auth blueprint in `auth.py`.
- It loads the vector store once at startup and reuses it for queries and ingestion.
- The chat route uses Server-Sent Events (SSE) to stream pipeline trace steps and final responses.
- The upload route saves files, ingests them into the vector store, and returns metadata.

### 3. Authentication and Authorization

- `auth.py` implements registration, login, refresh, logout, and logout-all.
- `db.py` stores user accounts, hashed passwords, refresh tokens, projects, conversations, and document metadata.
- JWT cookies are used alongside CSRF protection for secure state-changing calls.

### 4. Data Storage

- PostgreSQL is the main relational store for:
  - users, refresh tokens, auth audit events
  - projects, conversations, conversation titles
  - documents, document chunks, and document references
- `db.py` contains table creation helpers and CRUD access methods.
- `graph_store.py` optionally mirrors documents and chunks into a Neo4j graph store for richer relationships.

### 5. Ingestion and Embedding

- `ingestion.py` processes uploaded files or local documents into chunks.
- Supported file types include:
  - PDF (`.pdf`)
  - Word documents (`.docx`)
  - text files (`.txt`)
  - audio/video (`.mp3`, `.wav`, `.mp4`)
- PDF pages are loaded with PyMuPDF (`fitz`), and pages containing meaningful images can be sent through a vision extraction flow.
- Audio and video files are transcribed with Whisper.
- Documents are split into chunks using `RecursiveCharacterTextSplitter`.
- Chunks are embedded into a Chroma vector store using `BAAI/bge-base-en-v1.5` embeddings.
- Uploaded documents are also persisted to PostgreSQL and Neo4j via `persist_document_to_db_and_graph`.

### 6. Retrieval and Re-ranking

- `retrieval.py` handles vector search against the Chroma store.
- It searches using both semantic and keyword queries, then reranks with Reciprocal Rank Fusion (RRF).
- Retrieval is scoped by project via metadata filters so each project can maintain separate search contexts.
- The result is a ranked list of document chunks with source, page, file path, and score metadata.

### 7. Query Rewriting

- `query_rewriter.py` rewrites user queries to make them more effective for retrieval.
- It uses an LLM (`call_gemini` through `llm_client.py`) to generate self-contained, retrievable queries.
- If retrieval fails, it can re-write the query with feedback from the evaluator.

### 8. Orchestration

- `orchestrator.py` decides whether a query should use the RAG path or a direct answer path.
- It uses keyword routing for many cases, with explicit direct and RAG patterns.
- For ambiguous queries, it calls an LLM router that returns either `RAG` or `DIRECT`.
- The orchestrator also detects explicit web search requests and routes them to a direct web-search aware response instead of RAG.

### 9. Document Evaluation

- `evaluator.py` determines whether the retrieved chunks are relevant enough.
- It sends the query, rewritten query, and retrieved chunks to an LLM evaluator.
- The evaluator returns JSON with `relevant: true|false` and feedback.
- If documents are judged irrelevant, the pipeline retries with a refined query.

### 10. Response Generation

- `response_generator.py` creates the final assistant answer.
- It can generate:
  - direct responses when RAG is not needed
  - grounded responses when retrieval is used
  - safe fallback responses when retrieval fails repeatedly
- Responses are produced by a Groq-compatible LLM via `llm_client.py`.
- Grounded responses are expected to cite retrieved sources and respect the retrieved document content.

### 11. File Generation

- `tools/file_generator.py` can generate `.pdf` or `.docx` outputs from query results.
- It uses Groq chat completions to generate document content and ReportLab / python-docx to write files.
- The pipeline may generate files if the user request includes a file generation intent.

---

## End-to-End Query Flow

1. User submits a chat message through the frontend.
2. The frontend calls `POST /api/chat` with `session_id` and `message`.
3. `app.py` validates the session and user ownership.
4. The request enters `pipeline.run_pipeline_stream(user_query, session_id)`.
5. `pipeline._run_pipeline_steps` begins executing the following stages:
   - Load conversation history from PostgreSQL.
   - Rewrite the query with `query_rewriter.rewrite_query`.
   - Decide retrieval vs direct answer with `orchestrator.needs_rag`.

6. If the query is direct:
   - Optionally perform a live web search if the query explicitly asks for web/latest information.
   - Call `response_generator.generate_direct_response`.
   - Save the user and assistant messages to the conversation.
   - Return a `done` event with the answer.

7. If the query requires RAG:
   - Retrieve candidate document chunks with `retrieval.retrieve_with_scores`.
   - Evaluate relevance with `evaluator.evaluate_documents`.
   - If relevant:
     - Call `response_generator.generate_grounded_response`.
     - Save messages and emit the final answer.
   - If not relevant:
     - Use `rewrite_query_with_feedback` to adjust the retrieval query.
     - Retry retrieval up to `MAX_RETRIES`.
     - If still failing, generate a safe fallback answer.

8. Every pipeline stage emits trace steps.
9. `app.py` streams trace `step` events and the final `done` event back to the browser.
10. The frontend renders the answer plus an expandable trace sidebar showing:
   - history load
   - query rewrite diffs
   - orchestration decision
   - retrieved documents previews
   - evaluator relevance verdict
   - final generation type

---

## Document Upload Flow

1. The user uploads a file via the frontend.
2. `app.py` saves the file to `uploads/<project_id or general>/...`.
3. `ingestion.ingest_uploaded_file` processes the file:
   - PDFs are parsed page-by-page, with image pages optionally processed through vision extraction.
   - `.docx` and `.txt` files are loaded directly.
   - Audio/video files are transcribed.
   - Each chunk gets metadata such as `source`, `file_path`, `page`, `project_id`, and `document_id`.
4. The file is registered in the relational database and Neo4j graph store.
5. Document chunks are split and embedded into the Chroma vector store.
6. The new content becomes available to subsequent retrieval queries.

---

## Core Components and Files

- `app.py`: Flask routes, SSE chat streaming, file upload route, auth integration.
- `auth.py`: authentication blueprint, JWT creation, refresh/logout flows.
- `db.py`: PostgreSQL table management, user/project/conversation/document persistence.
- `pipeline.py`: main pipeline orchestration, streaming trace support, chat CLI fallback.
- `orchestrator.py`: decides RAG vs direct answer path.
- `query_rewriter.py`: rewrites ambiguous or context-dependent queries for retrieval.
- `retrieval.py`: vector search, keyword+semantic search, RRF reranking.
- `evaluator.py`: document relevance evaluation via LLM.
- `response_generator.py`: final answer generation for direct, grounded, and fallback responses.
- `ingestion.py`: file loading, chunking, embedding, document registration.
- `llm_client.py`: Groq-compatible LLM client wrapper.
- `graph_store.py`: Neo4j document graph persistence.
- `web_search_mcp.py`: optional web search tool integration.
- `tools/file_generator.py`: helper to generate downloadable PDF/DOCX outputs.

---

## Key Design Principles

- `Trace-first`: every chat query records the pipeline stages so users can inspect why a response was produced.
- `RAG fallback`: the system can answer directly for simple queries and only uses retrieval when needed.
- `Scoped retrieval`: document chunks are filtered by project scope to keep knowledge bases isolated.
- `Query refinement`: when retrieved documents are not relevant, the pipeline adapts with evaluator feedback.
- `Persistence`: messages, documents, and metadata are stored in PostgreSQL and optionally mirrored into Neo4j.
- `Modular LLM usage`: separate specialization for rewriting, routing, evaluation, and response generation.

---

## Deployment and Run Notes

### Environment variables

The app depends on environment configuration including:

- `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `FLASK_SECRET_KEY`, `JWT_SECRET_KEY`
- `GROQ_API_KEY`
- `OPENAI_API_KEY` (for vision extraction and transcription flows)
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- `TAVILY_API_KEY` (optional, for web search)
- `ORCHESTRATOR_MODEL`, `REWRITER_MODEL`, `RESPONSE_MODEL`, `EVALUATOR_MODEL`

### Local startup

1. Activate the Python environment.
2. Install dependencies from `requirements.txt`.
3. Start the app with:

```bash
python app.py
```

### Health endpoint

- `GET /api/health` returns `vectorstore_loaded` status.

---

## Suggested Presentation Flow

1. Introduce the goal: a secure RAG chat assistant over user-uploaded documents.
2. Show the front-end experience: login, conversation/project selection, chat, document upload, trace.
3. Explain the backend architecture in layers: auth, storage, ingestion, retrieval, orchestration, generation.
4. Walk through a sample query path with direct and RAG scenarios.
5. Describe upload ingestion and how the vector store is built.
6. Highlight trace visibility and retry logic.
7. Close with environment, running locally, and extension points.

---

## Extension Opportunities

- Add more supported file formats (Excel, PPTX, HTML).
- Add semantic document preview / chunk browsing in the UI.
- Add model selection per project or per conversation.
- Add a knowledge graph query layer using Neo4j relationships.
- Improve LLM prompt templates for better hallucination control.
- Add user/role-based access control for projects and documents.

---

## Notes

This presentation is based on the current repository structure and module implementations as of the inspected files.
