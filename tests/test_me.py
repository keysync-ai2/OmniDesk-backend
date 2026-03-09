"""Local test for auth me Lambda."""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATABASE_URL"] = (
    "postgresql://neondb_owner:npg_rmhlnp2twyM8"
    "@ep-aged-frost-ad88w6kz-pooler.c-2.us-east-1.aws.neon.tech"
    "/neondb?sslmode=require"
)
os.environ["JWT_SECRET"] = "test-secret-key-for-local-dev-only-32chars!"

from lambdas.auth.login import lambda_handler as login_handler
from lambdas.auth.me import lambda_handler as me_handler

def test(name, event, expected_status):
    result = me_handler(event, None)
    status = result["statusCode"]
    body = json.loads(result["body"]) if result.get("body") else {}
    passed = status == expected_status
    print(f"{'PASS' if passed else 'FAIL'} | {name} | {status} | {json.dumps(body, default=str)[:120]}")
    return passed

print("=" * 80)
print("Testing: omnidesk-auth-me")
print("=" * 80)

all_passed = True

# Test 1: No auth header
all_passed &= test("No auth header", {"httpMethod": "GET", "headers": {}}, 401)

# Test 2: Invalid token
all_passed &= test("Invalid token", {"httpMethod": "GET", "headers": {"Authorization": "Bearer invalid.token.here"}}, 401)

# Login first to get a valid token
login_result = login_handler({"httpMethod": "POST", "body": json.dumps({"email": "admin@omnidesk.test", "password": "Admin@1234"})}, None)
token = json.loads(login_result["body"])["access_token"]

# Test 3: Valid token
all_passed &= test("Valid token", {"httpMethod": "GET", "headers": {"Authorization": f"Bearer {token}"}}, 200)

print("=" * 80)
print(f"Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
