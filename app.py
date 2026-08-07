import os
import json
import logging
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_file, url_for
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity, get_jwt

import db
from db import (
    create_conversation,
    delete_conversation,
    load_conversation_history,
    load_conversations,
    update_conversation_title,
    get_conversation_title,
    conversation_belongs_to_user,
)
from retrieval import load_vectorstore
from pipeline import run_pipeline_stream
from agent_harness import run_agent_harness_stream
from auth import auth_bp, bcrypt

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "mehreen'skey")

# --- JWT / cookie session configuration -------------------------------------
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "my-very-secret-key")
app.config["JWT_TOKEN_LOCATION"] = ["cookies"]
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)
app.config["JWT_REFRESH_TOKEN_EXPIRES"] = timedelta(days=30)
app.config["JWT_ACCESS_COOKIE_PATH"] = "/api/"
app.config["JWT_REFRESH_COOKIE_PATH"] = "/api/auth/refresh"
# Set FLASK_ENV=production (and serve over https) to require secure cookies.
app.config["JWT_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["JWT_COOKIE_CSRF_PROTECT"] = True
app.config["JWT_BLOCKLIST_ENABLED"] = True
app.config["JWT_BLOCKLIST_TOKEN_CHECKS"] = ["refresh"]  # access tokens are short-lived & stateless

jwt = JWTManager(app)
bcrypt.init_app(app)


@jwt.token_in_blocklist_loader
def check_if_revoked(jwt_header, jwt_payload):
    if jwt_payload.get("type") != "refresh":
        return False
    return db.is_refresh_token_revoked(jwt_payload["jti"])


app.register_blueprint(auth_bp, url_prefix="/api/auth")

logging.basicConfig(level=logging.INFO)
log = app.logger

try:
    db.ensure_auth_tables()
    db.ensure_project_tables()
    db.ensure_trace_steps_table()
except Exception:
    log.exception("Could not ensure auth tables — check DB connection/env vars.")


vectorstore = None
try:
    log.info("Loading vectorstore...")
    vectorstore = load_vectorstore()
    log.info("Vectorstore ready.")
except Exception:
    log.exception("Vectorstore failed to load.")


def _make_title(text):
    text = " ".join(text.strip().split())
    return (text[:42] + "…") if len(text) > 42 else (text or "New chat")


def _serialize_history(history):
    """Legacy shape used only if callers still pass (role, content) tuples."""
    messages = []
    if not history:
        return messages
    for item in history:
        if len(item) >= 2:
            role, content = item[0], item[1]
        else:
            continue
        if role in ("user", "assistant") and content and content.strip():
            messages.append({"role": role, "content": content})
    return messages


def _serialize_conversations(rows):
    return [
        {"session_id": str(row[0]), "title": row[1], "created_at": str(row[2])}
        for row in rows
    ]


@app.route("/")
def index():
    return render_template("index.html")

@app.after_request
def add_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    return response



# They've been replaced by the auth blueprint at:
#   POST /api/auth/register  { username, password, email? }
#   POST /api/auth/login     { email, password }
#   POST /api/auth/refresh
#   POST /api/auth/logout
#   POST /api/auth/logout-all
#   GET  /api/auth/me


@app.route("/api/conversations", methods=["POST"])
@jwt_required()
def new_conversation():
    user_id = int(get_jwt_identity())
    session_id = create_conversation(user_id)
    return jsonify({"session_id": str(session_id), "title": "New chat"})

from werkzeug.utils import secure_filename
from ingestion import ingest_uploaded_file

ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".txt", ".mp4", ".mp3", ".wav"}
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)



@app.route("/api/projects/<project_id>/conversations", methods=["GET"])
@jwt_required()
def project_conversations(project_id):
    user_id = int(get_jwt_identity())
    project = db.get_project_by_id(project_id, user_id)
    if not project:
        return jsonify({"error": "Project not found."}), 404
    rows = db.load_conversations_by_scope(user_id, project_id=project_id)
    return jsonify({"project": project, "conversations": _serialize_conversations(rows)})

@app.route("/api/me")
@jwt_required()
def me():
    try:
        identity = get_jwt_identity()
    except Exception:
        identity = None

    if not identity:
        return jsonify({"logged_in": False})

    user_id = int(identity)
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"logged_in": False})

    conversations = _serialize_conversations(db.load_conversations_by_scope(user_id, project_id=None))
    projects = [
        {
            "id": r["id"],
            "name": r.get("name"),
            "description": r.get("description"),
            "created_at": str(r.get("created_at")) if r.get("created_at") else None,
        }
        for r in db.load_projects(user_id)
    ]
    return jsonify({
        "logged_in": True,
        "username": user["username"],
        "conversations": conversations,
        "projects": projects,
    })

@app.route("/api/conversations/<session_id>", methods=["DELETE"])
@jwt_required()
def remove_conversation(session_id):
    user_id = int(get_jwt_identity())
    if not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404

    project_id = db.get_conversation_project_id(session_id)
    delete_conversation(session_id)

    remaining = db.load_conversations_by_scope(user_id, project_id=project_id)
    if not remaining:
        create_conversation(user_id, project_id=project_id)
        remaining = db.load_conversations_by_scope(user_id, project_id=project_id)

    return jsonify({"conversations": _serialize_conversations(remaining), "project_id": project_id})

@app.route("/api/projects/<project_id>/conversations", methods=["POST"])
@jwt_required()
def new_project_conversation(project_id):
    user_id = int(get_jwt_identity())
    project = db.get_project_by_id(project_id, user_id)
    if not project:
        return jsonify({"error": "Project not found."}), 404
    session_id = db.create_conversation(user_id, title="New chat", project_id=project_id)
    return jsonify({"session_id": str(session_id), "title": "New chat"})

@app.route("/api/conversations/<session_id>/upload", methods=["POST"])
@jwt_required()
def upload_document(session_id):
    user_id = int(get_jwt_identity())
    if not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file part."}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    filename = secure_filename(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": f"Unsupported file type: {ext}"}), 400

    project_id = db.get_conversation_project_id(session_id)
    dest_dir = os.path.join(UPLOAD_DIR, str(project_id) if project_id else "general")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)
    file.save(dest_path)

    try:
        document_id = ingest_uploaded_file(
            dest_path, filename,
            project_id=project_id,
            uploaded_by_user_id=user_id,
            vectorstore=vectorstore,   # reuse the global one already loaded at startup
        )
    except Exception:
        log.exception("Failed to ingest uploaded document")
        return jsonify({"error": "Failed to process document."}), 500

    return jsonify({"document_id": document_id, "filename": filename, "project_id": project_id})

@app.route("/api/conversations/<session_id>/messages")
@jwt_required()
def conversation_messages(session_id):
    user_id = int(get_jwt_identity())
    if not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404

    messages = db.load_messages_with_traces(session_id)
    return jsonify({"messages": messages})



@app.route("/api/chat", methods=["POST"])
@jwt_required()
def chat():
    user_id = int(get_jwt_identity())

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    user_query = (data.get("message") or "").strip()
    use_web_search = bool(data.get("web_search"))
    agent_mode = bool(data.get("agent_mode"))

    if not session_id or not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404
    if not user_query:
        return jsonify({"error": "Message can't be empty."}), 400

    is_first_message = len(load_conversation_history(session_id) or []) == 0

    def sse(payload):
        return f"data: {json.dumps(payload)}\n\n"

    def event_stream():
        try:
            stream = (
                run_agent_harness_stream(user_query, session_id, use_web_search=use_web_search)
                if agent_mode
                else run_pipeline_stream(user_query, session_id, use_web_search=use_web_search)
            )
            for event in stream:
                if event["type"] == "step":
                    yield sse({"type": "step", "step": event["step"]})

                elif event["type"] == "done":
                    if is_first_message:
                        update_conversation_title(session_id, _make_title(user_query))
                    title = get_conversation_title(session_id)

                    yield sse({
                        "type": "done",
                        "response": event["response"],
                        "file": event.get("file"),
                        "rag_used": event["rag_used"],
                        "citations": event.get("citations", []),
                        "agent_mode": event.get("agent_mode", agent_mode),
                        "title": title,
                    })

                elif event["type"] == "error":
                    yield sse({"type": "error", "error": event.get("error") or "Unknown error."})
        except Exception as exc:
            log.exception("Pipeline error")
            yield sse({"type": "error", "error": f"Pipeline error: {exc}"})

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_local_file_path(file_path, filename=None):
    """
    Resolve a documents.file_path that may be relative (documents\\foo.pdf)
    or an absolute path from another checkout. Prefer an existing file under
    this project's documents/ or uploads/ folders.
    """
    name = filename or (os.path.basename(file_path) if file_path else None)
    candidates = []
    if file_path:
        candidates.append(file_path)
        if not os.path.isabs(file_path):
            candidates.append(os.path.join(BASE_DIR, file_path))
    if name:
        candidates.append(os.path.join(BASE_DIR, "documents", name))
        candidates.append(os.path.join(BASE_DIR, "uploads", "general", name))
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = os.path.normpath(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(normalized):
            return normalized
    return None


@app.route("/docs/<doc_id>")
def doc_detail(doc_id):
    doc = db.get_document_by_id(int(doc_id)) if doc_id.isdigit() else None
    if not doc:
        return render_template("doc.html", doc_id=doc_id, doc=None, error="Document not found."), 404

    filename = doc.get("filename") or (os.path.basename(doc.get("file_path") or "") if doc.get("file_path") else None)
    file_path = _resolve_local_file_path(doc.get("file_path"), filename)
    if not file_path:
        return render_template(
            "doc.html",
            doc_id=doc_id,
            doc=doc,
            error="This document is not available on the server.",
        )

    filename = filename or os.path.basename(file_path)
    raw = request.args.get("raw") == "1"
    page = request.args.get("page", type=int)
    chunk = request.args.get("chunk")
    is_pdf = filename.lower().endswith(".pdf")

    if not raw and page is not None and is_pdf:
        return render_template(
            "viewer.html",
            doc=doc,
            doc_id=doc_id,
            page=page,
            chunk=chunk,
            filename=filename,
            pdf_url=url_for("doc_detail", doc_id=doc_id, raw=1),
        )

    return send_file(file_path, as_attachment=False, download_name=filename)

@app.route("/download/generated/<path:filename>")
def download_generated_file(filename):
    safe_filename = os.path.basename(filename)
    generated_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_files")
    path = os.path.join(generated_dir, safe_filename)
    path = os.path.normpath(path)
    if not path.startswith(os.path.normpath(generated_dir) + os.sep):
        return jsonify({"error": "Invalid file path."}), 400
    if not os.path.exists(path):
        return jsonify({"error": "File not found."}), 404
    return send_file(path, as_attachment=True, download_name=safe_filename)

@app.route("/api/projects", methods=["POST"])
@jwt_required()
def new_project():
    user_id = int(get_jwt_identity())
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip() or "Untitled project"
    project_id = db.create_project(user_id, name, data.get("description"))
    return jsonify({"id": project_id, "name": name})

@app.route("/api/projects", methods=["GET"])
@jwt_required()
def list_projects():
    user_id = int(get_jwt_identity())
    return jsonify({"projects": db.load_projects(user_id)})


def _purge_project_chroma(project_id):
    """Remove vector chunks tagged with this project_id (int or str metadata)."""
    if vectorstore is None:
        return
    collection = getattr(vectorstore, "_collection", None)
    if collection is None:
        return
    candidates = [str(project_id)]
    try:
        candidates.append(int(project_id))
    except (TypeError, ValueError):
        pass
    for pid in candidates:
        try:
            collection.delete(where={"project_id": pid})
        except Exception:
            log.exception("Chroma purge failed for project_id=%r", pid)


def _purge_project_files(project_id, file_paths):
    """Delete uploaded files listed in DB plus the uploads/<project_id> folder."""
    import shutil

    for path in file_paths or []:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            log.warning("Could not delete document file: %s", path)

    project_upload_dir = os.path.join(UPLOAD_DIR, str(project_id))
    if os.path.isdir(project_upload_dir):
        try:
            shutil.rmtree(project_upload_dir)
        except OSError:
            log.exception("Could not remove upload dir %s", project_upload_dir)


@app.route("/api/projects/<project_id>", methods=["DELETE"])
@jwt_required()
def remove_project(project_id):
    """
    Delete project + its conversations/messages/traces + project documents,
    then purge Chroma chunks and on-disk uploads for that project.
    """
    # Guard against junk path segments that would otherwise look like "not found".
    if not str(project_id).isdigit():
        return jsonify({
            "error": f"Invalid project id '{project_id}'. Expected a numeric id.",
        }), 400

    user_id = int(get_jwt_identity())
    try:
        result = db.delete_project(int(project_id), user_id)
        if result is None:
            return jsonify({"error": "Project not found."}), 404

        _purge_project_files(project_id, result.get("file_paths"))
        _purge_project_chroma(project_id)
        _purge_project_neo4j(result.get("neo4j_ids") or [])

        return jsonify({
            "ok": True,
            "deleted_project_id": int(project_id),
            "deleted_conversations": len(result.get("conversation_ids") or []),
            "deleted_files": len(result.get("file_paths") or []),
        })
    except Exception as exc:
        log.exception("Project delete failed for project_id=%s user_id=%s", project_id, user_id)
        return jsonify({"error": f"Delete failed: {exc}"}), 500


def _purge_project_neo4j(neo_ids):
    """Best-effort graph cleanup; never block delete on Neo4j being down."""
    neo_ids = [str(n) for n in (neo_ids or []) if n]
    if not neo_ids:
        return
    try:
        from graph_store import NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, get_graph_store
        if not (NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD):
            return
        store = get_graph_store()
        try:
            # Short timeout so a dead Neo4j cannot hang the HTTP request.
            store.driver.verify_connectivity()
            with store.driver.session() as session:
                for neo_id in neo_ids:
                    session.run(
                        """
                        MATCH (d:Document {id: $id})
                        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
                        DETACH DELETE c, d
                        """,
                        id=neo_id,
                    )
        finally:
            store.close()
    except Exception:
        log.exception("Neo4j cleanup skipped/failed for %s document(s)", len(neo_ids))


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "vectorstore_loaded": vectorstore is not None})


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True,use_reloader=False)