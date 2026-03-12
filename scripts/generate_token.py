"""Generate a fresh JWT access token for OmniDesk MCP.

Usage:
    python scripts/generate_token.py
    python scripts/generate_token.py --email admin@omnidesk.test --password Admin@1234
    python scripts/generate_token.py --url https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev
"""
import argparse
import json
import urllib.request
import urllib.error

DEFAULT_URL = "https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev"
DEFAULT_EMAIL = "admin@omnidesk.test"
DEFAULT_PASSWORD = "Admin@1234"


def generate_token(base_url, email, password):
    url = f"{base_url}/api/auth/login"
    payload = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Login failed ({e.code}): {body}")
        return None

    token = data.get("access_token")
    if not token:
        print(f"Unexpected response: {json.dumps(data, indent=2)}")
        return None

    print(f"\nAccess Token (48h):\n{token}\n")
    print(f"MCP URL with token:\n{base_url}/mcp?token={token}\n")
    print(f"Claude Desktop config (query param):")
    print(json.dumps({
        "command": "npx",
        "args": ["mcp-remote", f"{base_url}/mcp?token={token}"],
    }, indent=2))
    return token


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate OmniDesk JWT token")
    parser.add_argument("--url", default=DEFAULT_URL, help="API Gateway base URL")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Login email")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Login password")
    args = parser.parse_args()
    generate_token(args.url, args.email, args.password)
