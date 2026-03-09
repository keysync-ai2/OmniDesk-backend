"""Local test for MCP server Lambda."""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.mcp.server import lambda_handler

def test(name, event, expected_check):
    result = lambda_handler(event, None)
    body = json.loads(result["body"]) if result.get("body") else {}
    passed = expected_check(result, body)
    print(f"{'PASS' if passed else 'FAIL'} | {name} | {result['statusCode']} | {json.dumps(body, default=str)[:120]}")
    return passed

def make_rpc(method, params=None, req_id=1):
    return {"httpMethod": "POST", "body": json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})}

print("=" * 80)
print("Testing: omnidesk-mcp-server")
print("=" * 80)

all_passed = True

# OPTIONS
all_passed &= test("CORS preflight", {"httpMethod": "OPTIONS"}, lambda r, b: r["statusCode"] == 204)

# initialize
all_passed &= test("initialize", make_rpc("initialize"),
    lambda r, b: b.get("result", {}).get("protocolVersion") == "2025-03-26"
        and b.get("result", {}).get("serverInfo", {}).get("name") == "omnidesk-mcp")

# tools/list
all_passed &= test("tools/list", make_rpc("tools/list"),
    lambda r, b: b.get("result", {}).get("tools") == [])

# ping
all_passed &= test("ping", make_rpc("ping"),
    lambda r, b: "result" in b)

# notifications/initialized
all_passed &= test("notifications/initialized", make_rpc("notifications/initialized"),
    lambda r, b: r["statusCode"] == 200)

# unknown tool
all_passed &= test("unknown tool", make_rpc("tools/call", {"name": "nonexistent"}),
    lambda r, b: b.get("error", {}).get("code") == -32602)

# unknown method
all_passed &= test("unknown method", make_rpc("unknown/method"),
    lambda r, b: b.get("error", {}).get("code") == -32601)

print("=" * 80)
print(f"Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
