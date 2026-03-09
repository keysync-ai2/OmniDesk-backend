"""Lambda: omnidesk-auth-login
POST /api/auth/login
Input:  {email, password}
Output: {access_token, refresh_token, user: {user_id, email, full_name, role}}
"""
import json
import bcrypt
from utils.db import get_connection
from utils.response import success, error
from utils.jwt_helper import create_access_token, create_refresh_token
from utils.audit import log_action


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("Invalid JSON body", 400)

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return error("Email and password are required", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, email, password_hash, full_name, role, is_active
            FROM users WHERE email = %s
            """,
            (email,),
        )
        row = cur.fetchone()

        if not row:
            return error("Invalid email or password", 401)

        user_id, user_email, password_hash, full_name, role, is_active = row

        if not is_active:
            return error("Account is deactivated", 403)

        if not bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8")):
            return error("Invalid email or password", 401)

        access_token = create_access_token(user_id, user_email, role)
        refresh_token = create_refresh_token(user_id)

        log_action(str(user_id), "login", "auth", entity_id=str(user_id),
                   details={"email": user_email, "role": role})

        return success({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "user_id": str(user_id),
                "email": user_email,
                "full_name": full_name,
                "role": role,
            },
        })
    except Exception as e:
        return error(f"Login failed: {str(e)}", 500)
    finally:
        conn.close()
