# Agentic RAG Chat App

## What this project does
This app is a Flask-based chat interface for asking questions against uploaded documents. It uses a retrieval-augmented generation (RAG) pipeline to answer questions with context from a vector store and a conversation history.

## Main pieces
- app.py: Flask routes for login, chat, conversations, and document reference pages.
- db.py: PostgreSQL access for users, conversations, messages, and conversation metadata.
- pipeline.py: Main orchestration flow for the chat pipeline.
- orchestrator.py: Chooses whether a query needs retrieval or can be answered directly.
- query_rewriter.py: Rewrites user questions so they are more useful for retrieval.
- retrieval.py: Builds and queries the vector store for document chunks.
- response_generator.py: Produces the final assistant reply.
- evaluator.py: Checks whether retrieved documents are relevant to the query.
- static/js/app.js: Frontend chat experience, streaming responses, trace panel, and expandable reasoning UI.
- templates/: HTML templates for the app UI and document reference pages.

## Recent changes
- Switched the LLM calls from the old local/ngrok-based Qwen setup to a shared client that uses Groq-compatible models.
- Added an expandable “Show thinking” section beneath assistant replies so the user can inspect the pipeline summary when desired.
- Added a trace panel and message-level trace pills so the conversation UI can show the pipeline steps for each reply.
- Added a document reference placeholder route so references like [Document 1] can be clicked and opened later as real source pages.
- Hardened the frontend so markdown rendering and message display continue to work even when external libraries are unavailable.

## Suggested database design for uploaded PDFs
For runtime uploads, the best approach is to store both the file metadata and its extracted content separately.

### Recommended tables
1. documents
   - id (primary key)
   - filename
   - original_name
   - mime_type
   - file_path_or_bucket_key
   - uploaded_at
   - uploaded_by_user_id
   - title
   - summary (optional)

2. document_chunks
   - id (primary key)
   - document_id (foreign key)
   - chunk_index
   - text
   - page_number
   - embedding (optional if you want semantic search directly in DB)
   - created_at

3. document_references
   - id (primary key)
   - message_id (foreign key to messages)
   - document_id (foreign key)
   - chunk_id (optional)
   - cited_text (optional)
   - created_at

### Why this structure works
- documents keeps the source file and its metadata.
- document_chunks lets the retrieval layer store chunk-level context for search.
- document_references links citations in chat replies back to the specific uploaded document or chunk.

### Recommended storage options
- Local development: store PDFs in a folder such as uploads/ and keep the path in the database.
- Production: use S3, Azure Blob Storage, or GCS and store the object key in the database.

### For navigable references
When a reply cites a document, the app should:
- create a citation marker such as [Document 1]
- resolve that marker to the correct document record in the DB
- link the UI to a page or endpoint that opens the document preview or source metadata

## How the project works in practice
1. A user logs in.
2. The app creates or loads a conversation.
3. The user sends a message.
4. The pipeline checks whether the query needs retrieval.
5. If needed, the app retrieves relevant document chunks from the vector store.
6. The answer is generated and saved into the conversation history.
7. The UI shows the response and the trace/ reasoning summary when requested.

## Environment variables
The app expects:
- DB_HOST, DB_NAME, DB_USER, DB_PASSWORD
- GROQ_API_KEY
- optionally: ORCHESTRATOR_MODEL, REWRITER_MODEL, RESPONSE_MODEL, EVALUATOR_MODEL

## Running locally
1. Activate the virtual environment.
2. Install dependencies from requirements.txt.
3. Start the Flask app with python app.py.
