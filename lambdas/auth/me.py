"""Lambda: omnidesk-auth-me
GET /api/auth/me
Input:  Authorization: Bearer <jwt>
Output: {user_id, email, full_name, phone, role, created_at}
"""
from utils.db import get_connection
from utils.response import success, error
from utils.jwt_helper import verify_token


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)

    # Extract token from Authorization header
    auth_header = (event.get("headers") or {}).get("Authorization") or \
                  (event.get("headers") or {}).get("authorization") or ""
    if not auth_header.startswith("Bearer "):
        return error("Missing or invalid Authorization header", 401)

    token = auth_header[7:]
    payload = verify_token(token, expected_type="access")
    if not payload:
        return error("Invalid or expired token", 401)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, email, full_name, phone, role, is_active, created_at
            FROM users WHERE id = %s
            """,
            (payload["user_id"],),
        )
        row = cur.fetchone()

        if not row:
            return error("User not found", 404)

        if not row[5]:  # is_active
            return error("Account is deactivated", 403)

        return success({
            "user_id": str(row[0]),
            "email": row[1],
            "full_name": row[2],
            "phone": row[3],
            "role": row[4],
            "created_at": str(row[6]),
        })
    except Exception as e:
        return error(f"Failed to fetch profile: {str(e)}", 500)
    finally:
        conn.close()
