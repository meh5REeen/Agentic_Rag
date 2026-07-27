import os
import json
from dotenv import load_dotenv
import psycopg2

load_dotenv()

def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=int(os.getenv("DB_PASSWORD")),
    )


def ensure_document_tables():
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
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
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER,
                    page_number INTEGER,
                    text TEXT,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_references (
                    id SERIAL PRIMARY KEY,
                    message_id INTEGER,
                    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_id INTEGER REFERENCES document_chunks(id) ON DELETE SET NULL,
                    citation TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
    finally:
        conn.close()

def ensure_project_tables():
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
            cur.execute("CREATE INDEX IF NOT EXISTS conversations_project_id_idx ON conversations (project_id)")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth tables (users extension, refresh tokens, audit log)
# ---------------------------------------------------------------------------

def ensure_auth_tables():
    """Idempotent — safe to call on every app startup."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()")
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS users_email_unique_idx
                ON users (email) WHERE email IS NOT NULL
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    id SERIAL PRIMARY KEY,
                    jti TEXT UNIQUE NOT NULL,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    revoked BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    user_agent TEXT,
                    ip_address TEXT
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS refresh_tokens_user_id_idx ON refresh_tokens (user_id)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_events (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    detail TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS auth_events_user_id_idx ON auth_events (user_id)"
            )
    finally:
        conn.close()


def create_user(username, password_hash, email=None):
    """Raises psycopg2.errors.UniqueViolation if username/email already exists."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, email)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (username, password_hash, email),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def get_user_by_username(username):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, email, is_active
                FROM users
                WHERE username = %s
                """,
                (username,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "username": row[1], "password_hash": row[2],
                "email": row[3], "is_active": row[4],
            }
    finally:
        conn.close()


def get_user_by_email(email):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, email, is_active
                FROM users
                WHERE email = %s
                """,
                (email,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0], "username": row[1], "password_hash": row[2],
                "email": row[3], "is_active": row[4],
            }
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, email, is_active
                FROM users
                WHERE id = %s
                """,
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "username": row[1], "email": row[2], "is_active": row[3]}
    finally:
        conn.close()


def store_refresh_token(jti, user_id, expires_at, user_agent=None, ip_address=None):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO refresh_tokens (jti, user_id, expires_at, user_agent, ip_address)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (jti, user_id, expires_at, user_agent, ip_address),
            )
    finally:
        conn.close()


def is_refresh_token_revoked(jti):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT revoked FROM refresh_tokens WHERE jti = %s", (jti,))
            row = cur.fetchone()
            return row is None or row[0] is True
    finally:
        conn.close()


def revoke_refresh_token(jti):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE refresh_tokens SET revoked = TRUE WHERE jti = %s", (jti,))
    finally:
        conn.close()


def revoke_all_user_tokens(user_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE refresh_tokens SET revoked = TRUE WHERE user_id = %s AND revoked = FALSE",
                (user_id,),
            )
    finally:
        conn.close()


def log_auth_event(event_type, user_id=None, ip_address=None, user_agent=None, detail=None):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO auth_events (user_id, event_type, ip_address, user_agent, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, event_type, ip_address, user_agent, detail),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Original functions (unchanged)
# ---------------------------------------------------------------------------

def add_new_message(conversation_id, role, content):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
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
    finally:
        conn.close()

def get_conversation_title(conversation_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT title FROM conversations WHERE id = %s",
                (conversation_id,)
            )
            row = cur.fetchone()
            return row[0] if row else "New chat"
    finally:
        conn.close()


def conversation_belongs_to_user(user_id, conversation_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM conversations
                WHERE id = %s AND user_id = %s
                """,
                (conversation_id, user_id)
            )
            return cur.fetchone() is not None
    finally:
        conn.close()

def get_conversation_project_id(conversation_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT project_id FROM conversations WHERE id = %s", (conversation_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def create_project(user_id, name, description=None):
    ensure_project_tables()
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (user_id, name, description) VALUES (%s, %s, %s) RETURNING id",
                (user_id, name, description),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()

def create_conversation(user_id, title="New Chat", project_id=None):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (user_id, title, project_id) VALUES (%s, %s, %s) RETURNING id",
                (user_id, title, project_id),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()

def register_document(uploaded_by_user_id, filename, file_path, title=None, mime_type="application/pdf", source_url=None, storage_type="local", content_hash=None, metadata=None, neo4j_id=None, project_id=None):
    ensure_document_tables()
    metadata = metadata or {}
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (
                    neo4j_id, title, filename, file_path, mime_type, storage_type,
                    source_url, content_hash, metadata, uploaded_by_user_id, project_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    neo4j_id,
                    title or os.path.splitext(filename)[0],
                    filename,
                    file_path,
                    mime_type,
                    storage_type,
                    source_url,
                    content_hash,
                    json.dumps(metadata),
                    uploaded_by_user_id,
                    project_id,
                ),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()
def load_conversations_by_scope(user_id, project_id=None):
    """project_id=None -> general chats (project_id IS NULL).
    project_id=<id> -> chats inside that project."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            if project_id is None:
                cur.execute(
                    """
                    SELECT id, title, created_at FROM conversations
                    WHERE user_id = %s AND project_id IS NULL
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, title, created_at FROM conversations
                    WHERE user_id = %s AND project_id = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id, project_id),
                )
            return cur.fetchall()
    finally:
        conn.close()


def get_project_by_id(project_id, user_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description FROM projects WHERE id = %s AND user_id = %s",
                (project_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "name": row[1], "description": row[2]}
    finally:
        conn.close()
def load_projects(user_id):
    ensure_project_tables()
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, description, created_at FROM projects WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "created_at": row[3],
                }
                for row in rows
            ]
    finally:
        conn.close()

def get_or_create_user_id(username):
    """Legacy no-password lookup. Kept for backward compatibility with any
    internal callers, but the /api/auth/* routes use create_user/get_user_by_username
    instead, since those enforce a password."""
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM users
                WHERE username = %s
                """,
                (username,),
            )

            row = cur.fetchone()

            if row:
                return row[0]

            cur.execute(
                """
                INSERT INTO users (username)
                VALUES (%s)
                RETURNING id
                """,
                (username,),
            )

            user_id = cur.fetchone()[0]
            return user_id

    finally:
        conn.close()

def update_conversation_title(conversation_id, title):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE conversations
                SET title = %s
                WHERE id = %s
                """,
                (title, conversation_id),
            )
    finally:
        conn.close()

def load_conversations(user_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, created_at
                FROM conversations
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            )

            return cur.fetchall()

    finally:
        conn.close()

def delete_conversation(conversation_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM conversations
                WHERE id = %s
                """,
                (conversation_id,)
            )
    finally:
        conn.close()

def delete_all_conversations(user_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM conversations
                WHERE user_id = %s
                """,
                (user_id,)
            )
    finally:
        conn.close()

def load_conversation_history(conversation_id):
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
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

    finally:
        conn.close()


def register_document_chunk(document_id, chunk_index, page_number, text, metadata=None):
    ensure_document_tables()
    metadata = metadata or {}
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_chunks (document_id, chunk_index, page_number, text, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (document_id, chunk_index, page_number, text, json.dumps(metadata)),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def add_document_reference(message_id, document_id, chunk_id=None, citation=None):
    ensure_document_tables()
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document_references (message_id, document_id, chunk_id, citation)
                VALUES (%s, %s, %s, %s)
                """,
                (message_id, document_id, chunk_id, citation),
            )
    finally:
        conn.close()


def get_document_by_id(document_id):
    ensure_document_tables()
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, neo4j_id, title, filename, file_path, mime_type, storage_type, source_url, content_hash, metadata, uploaded_by_user_id, created_at
                FROM documents
                WHERE id = %s
                """,
                (document_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "neo4j_id": row[1],
                "title": row[2],
                "filename": row[3],
                "file_path": row[4],
                "mime_type": row[5],
                "storage_type": row[6],
                "source_url": row[7],
                "content_hash": row[8],
                "metadata": row[9] or {},
                "uploaded_by_user_id": row[10],
                "created_at": row[11],
            }
    finally:
        conn.close()


def get_document_id_by_filename(filename):
    ensure_document_tables()
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM documents WHERE filename = %s ORDER BY id DESC LIMIT 1",
                (filename,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        conn = get_connection()
        print("✅ Successfully connected to PostgreSQL!")
        conn.close()
    except Exception as e:
        print(f"❌ Database connection failed: {e}")