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
- Refresh token rotation: every /refresh call revokes the old refresh token
  and issues a new one. If a revoked refresh token is ever replayed, the
  first check (`is_refresh_token_revoked`) blocks it.
- Every auth event (register, login success/fail, refresh, logout) is written
  to `auth_events` for audit/logging purposes.
"""
import psycopg2
from datetime import timedelta
from flask import Blueprint, request, jsonify
from flask_bcrypt import Bcrypt
from flask_jwt_extended import (
    create_access_token, create_refresh_token, decode_token,
    jwt_required, get_jwt, get_jwt_identity,
    set_access_cookies, set_refresh_cookies, unset_jwt_cookies,
)

import db

auth_bp = Blueprint("auth", __name__)
bcrypt = Bcrypt()


def _client_meta():
    return request.headers.get("User-Agent", "")[:255], request.remote_addr


from datetime import timedelta, datetime, timezone

def _issue_tokens(user_id):
    """Create a fresh access+refresh pair and register the refresh token."""
    access_token = create_access_token(identity=str(user_id))
    refresh_token = create_refresh_token(identity=str(user_id))

    decoded = decode_token(refresh_token)
    expires_at = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)  # <-- convert here

    user_agent, ip = _client_meta()
    db.store_refresh_token(
        jti=decoded["jti"],
        user_id=user_id,
        expires_at=expires_at,  # <-- pass datetime, not raw int
        user_agent=user_agent,
        ip_address=ip,
    )
    return access_token, refresh_token


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
        db.log_auth_event("register_failed", detail=f"duplicate username/email: {username}")
        return jsonify({"error": "Username or email already in use."}), 409

    db.log_auth_event("register", user_id=user_id)
    return jsonify({"message": "Account created.", "user_id": user_id}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("email") or data.get("username") or "").strip()
    password = data.get("password") or ""
    user_agent, ip = _client_meta()

    if "@" in identifier:
        user = db.get_user_by_email(identifier)
    else:
        user = db.get_user_by_username(identifier)

    if not user or not user["password_hash"] or not bcrypt.check_password_hash(user["password_hash"], password):
        db.log_auth_event("login_failed", ip_address=ip, user_agent=user_agent, detail=identifier)
        return jsonify({"error": "Invalid email or password."}), 401
    if not user["is_active"]:
        db.log_auth_event("login_failed", user_id=user["id"], ip_address=ip, user_agent=user_agent, detail="inactive account")
        return jsonify({"error": "This account is disabled."}), 403

    access_token, refresh_token = _issue_tokens(user["id"])
    db.log_auth_event("login_success", user_id=user["id"], ip_address=ip, user_agent=user_agent)

    resp = jsonify({"username": user["username"], "user_id": user["id"]})
    set_access_cookies(resp, access_token)
    set_refresh_cookies(resp, refresh_token)
    return resp, 200


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    user_id = int(get_jwt_identity())
    old_jti = get_jwt()["jti"]
    db.revoke_refresh_token(old_jti)  # rotation: old refresh token dies here

    access_token, refresh_token = _issue_tokens(user_id)
    user_agent, ip = _client_meta()
    db.log_auth_event("refresh", user_id=user_id, ip_address=ip, user_agent=user_agent)

    resp = jsonify({"message": "Token refreshed."})
    set_access_cookies(resp, access_token)
    set_refresh_cookies(resp, refresh_token)
    return resp, 200


@auth_bp.route("/logout", methods=["POST"])
@jwt_required(refresh=True)
def logout():
    user_id = int(get_jwt_identity())
    jti = get_jwt()["jti"]
    db.revoke_refresh_token(jti)
    db.log_auth_event("logout", user_id=user_id)

    resp = jsonify({"message": "Logged out."})
    unset_jwt_cookies(resp)
    return resp, 200


@auth_bp.route("/logout-all", methods=["POST"])
@jwt_required()
def logout_all():
    user_id = int(get_jwt_identity())
    db.revoke_all_user_tokens(user_id)
    db.log_auth_event("logout_all", user_id=user_id)

    resp = jsonify({"message": "All sessions revoked."})
    unset_jwt_cookies(resp)
    return resp, 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"id": user["id"], "username": user["username"], "email": user["email"]}), 200