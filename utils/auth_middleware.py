"""RBAC middleware — validates JWT and checks role permissions."""
from utils.response import error
from utils.jwt_helper import verify_token

# Role hierarchy: admin > manager > staff > viewer
ROLE_HIERARCHY = {"admin": 4, "manager": 3, "staff": 2, "viewer": 1}


def require_auth(handler, min_role="viewer"):
    """Decorator that validates JWT and enforces minimum role.

    Usage:
        def lambda_handler(event, context):
            ...
        lambda_handler = require_auth(lambda_handler, min_role="admin")
    """
    min_level = ROLE_HIERARCHY.get(min_role, 0)

    def wrapper(event, context):
        if event.get("httpMethod") == "OPTIONS":
            return handler(event, context)

        auth_header = (event.get("headers") or {}).get("Authorization") or \
                      (event.get("headers") or {}).get("authorization") or ""
        if not auth_header.startswith("Bearer "):
            return error("Missing or invalid Authorization header", 401)

        token = auth_header[7:]
        payload = verify_token(token, expected_type="access")
        if not payload:
            return error("Invalid or expired token", 401)

        user_role = payload.get("role", "viewer")
        user_level = ROLE_HIERARCHY.get(user_role, 0)
        if user_level < min_level:
            return error(f"Insufficient permissions. Required: {min_role}, your role: {user_role}", 403)

        # Attach user info to event for downstream use
        event["user"] = payload
        return handler(event, context)

    return wrapper
