import os
import json
import logging
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response, stream_with_context, send_file
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
from auth import auth_bp, bcrypt

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "mehreen'skey")

# --- JWT / cookie session configuration -------------------------------------
app.config["JWT_SECRET_KEY"] = os.environ.get("JWT_SECRET_KEY", "change-me-in-prod")
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

# Make sure auth tables exist on boot (idempotent).
try:
    db.ensure_auth_tables()
    db.ensure_project_tables()
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
    messages = []
    if not history:
        return messages
    for item in history:
        role, content = item
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


# NOTE: /api/login and /api/logout used to live here with no password check.
# They've been replaced by the auth blueprint at:
#   POST /api/auth/register  { username, password, email? }
#   POST /api/auth/login     { username, password }
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

    history = load_conversation_history(session_id)
    return jsonify({"messages": _serialize_history(history)})



@app.route("/api/chat", methods=["POST"])
@jwt_required()
def chat():
    user_id = int(get_jwt_identity())

    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    user_query = (data.get("message") or "").strip()
    use_web_search = bool(data.get("web_search"))

    if not session_id or not conversation_belongs_to_user(user_id, session_id):
        return jsonify({"error": "Conversation not found."}), 404
    if not user_query:
        return jsonify({"error": "Message can't be empty."}), 400

    is_first_message = len(load_conversation_history(session_id) or []) == 0

    def sse(payload):
        return f"data: {json.dumps(payload)}\n\n"

    def event_stream():
        try:
            for event in run_pipeline_stream(user_query, session_id, use_web_search=use_web_search):
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
                        "title": title,
                    })
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


@app.route("/docs/<doc_id>")
def doc_detail(doc_id):
    doc = db.get_document_by_id(int(doc_id)) if doc_id.isdigit() else None
    if not doc:
        return render_template("doc.html", doc_id=doc_id, doc=None, error="Document not found."), 404

    file_path = doc.get("file_path")
    if file_path and os.path.exists(file_path):
        return send_file(file_path, as_attachment=False, download_name=doc.get("filename") or os.path.basename(file_path))

    return render_template("doc.html", doc_id=doc_id, doc=doc, error="This document is not available on the server.")

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


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "vectorstore_loaded": vectorstore is not None})


if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True,use_reloader=False)