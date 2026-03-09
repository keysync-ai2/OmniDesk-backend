"""Lambda: omnidesk-mcp-server
POST /mcp
MCP JSON-RPC server — handles initialize, ping, tools/list, tools/call.
Stateless Lambda implementation (no SSE).

Auth flow:
  - JWT token is configured in Claude Desktop's mcp-remote headers
  - Every tools/call extracts user from the auth_token argument
  - If token is expired, user must regenerate via /api/auth/login and update config
  - Token expiry: 48 hours of inactivity (configurable in jwt_helper.py)
"""
import json
from utils.db import get_connection
from utils.jwt_helper import verify_token

SERVER_INFO = {
    "name": "omnidesk-mcp",
    "version": "1.0.0",
}

PROTOCOL_VERSION = "2025-03-26"

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id",
    "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
    "Mcp-Session-Id": "lambda-stateless",
}

# ── Tool Catalog ────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_profile",
        "description": "Get the current authenticated user's profile (name, email, role, phone).",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# ── Tool Handlers ───────────────────────────────────────────────────────


def handle_get_profile(args, user=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, full_name, phone, role, is_active, created_at FROM users WHERE id = %s",
            (user["user_id"],),
        )
        row = cur.fetchone()

        if not row or not row[5]:
            return {"error": "User not found or deactivated"}

        return {
            "user_id": str(row[0]),
            "email": row[1],
            "full_name": row[2],
            "phone": row[3],
            "role": row[4],
            "created_at": str(row[6]),
        }
    finally:
        conn.close()


TOOL_HANDLERS = {
    "get_profile": handle_get_profile,
}

# ── JSON-RPC Helpers ────────────────────────────────────────────────────


def jsonrpc_response(req_id, result):
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
    }


def jsonrpc_error(req_id, code, message):
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        ),
    }


def extract_user_from_headers(event):
    """Extract JWT user from Authorization header set by mcp-remote."""
    headers = event.get("headers") or {}
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        return verify_token(auth_header[7:], expected_type="access")
    return None


# ── Lambda Handler ──────────────────────────────────────────────────────


def lambda_handler(event, context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "")

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    if method == "GET":
        return {"statusCode": 405, "headers": CORS_HEADERS, "body": "SSE not supported in Lambda mode"}

    if method == "DELETE":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # Parse JSON-RPC
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return jsonrpc_error(None, -32700, "Parse error")

    req_id = body.get("id")
    rpc_method = body.get("method")
    params = body.get("params", {})

    # initialize
    if rpc_method == "initialize":
        return jsonrpc_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    # notifications/initialized
    if rpc_method == "notifications/initialized":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # tools/list
    if rpc_method == "tools/list":
        return jsonrpc_response(req_id, {"tools": TOOLS})

    # tools/call
    if rpc_method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            # Extract user from Authorization header
            user = extract_user_from_headers(event)
            if not user:
                return jsonrpc_response(req_id, {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "Authentication required. Your token is missing or expired. Please generate a new token via /api/auth/login and update your Claude Desktop config."
                    })}],
                    "isError": True,
                })

            result = handler(arguments, user=user)

            return jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            })
        except Exception as e:
            return jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            })

    # ping
    if rpc_method == "ping":
        return jsonrpc_response(req_id, {})

    return jsonrpc_error(req_id, -32601, f"Method not found: {rpc_method}")
