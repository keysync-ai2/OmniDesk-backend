"""Lambda: omnidesk-mcp-server
POST /mcp
MCP JSON-RPC server — handles initialize, ping, tools/list, tools/call.
Stateless Lambda implementation (no SSE).
"""
import json

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

# Tool catalog — empty for Phase 1, will grow in Phase 2+
TOOLS = []

# Tool handlers — empty for now
TOOL_HANDLERS = {}


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


def lambda_handler(event, context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "")

    # CORS preflight
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    # SSE not supported
    if method == "GET":
        return {"statusCode": 405, "body": "SSE not supported in Lambda mode"}

    # Session termination — no-op
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
            result = handler(arguments)
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
