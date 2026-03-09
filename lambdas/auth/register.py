"""Lambda: omnidesk-auth-register
POST /api/auth/register
Input:  {email, password, full_name, phone?, role?}
Output: {user_id, email, full_name, role}
"""
import json
import re
import bcrypt
from utils.db import get_connection
from utils.response import success, error
from utils.audit import log_action

VALID_ROLES = {"admin", "manager", "staff", "viewer"}
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def lambda_handler(event, context):
    # CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return error("Invalid JSON body", 400)

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    full_name = (body.get("full_name") or "").strip()
    phone = (body.get("phone") or "").strip() or None
    role = (body.get("role") or "staff").strip().lower()

    # Validation
    if not email or not EMAIL_RE.match(email):
        return error("Valid email is required", 400)
    if len(password) < 8:
        return error("Password must be at least 8 characters", 400)
    if not full_name:
        return error("Full name is required", 400)
    if role not in VALID_ROLES:
        return error(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}", 400)

    # Hash password
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Check duplicate email
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            return error("Email already registered", 409)

        # Insert user
        cur.execute(
            """
            INSERT INTO users (email, password_hash, full_name, phone, role)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, email, full_name, role, created_at
            """,
            (email, password_hash, full_name, phone, role),
        )
        row = cur.fetchone()
        conn.commit()

        user_id = str(row[0])
        log_action(user_id, "register", "auth", entity_id=user_id,
                   details={"email": row[1], "role": row[3]})

        return success(
            {
                "user_id": user_id,
                "email": row[1],
                "full_name": row[2],
                "role": row[3],
                "created_at": str(row[4]),
            },
            201,
        )
    except Exception as e:
        conn.rollback()
        return error(f"Registration failed: {str(e)}", 500)
    finally:
        conn.close()
