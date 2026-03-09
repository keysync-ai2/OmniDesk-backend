"""Local test for auth login Lambda."""
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

from lambdas.auth.login import lambda_handler
from utils.jwt_helper import verify_token

def test(name, event, expected_status):
    result = lambda_handler(event, None)
    status = result["statusCode"]
    body = json.loads(result["body"]) if result.get("body") else {}
    passed = status == expected_status
    print(f"{'PASS' if passed else 'FAIL'} | {name} | {status} | {json.dumps(body, default=str)[:120]}")
    return passed, body

print("=" * 80)
print("Testing: omnidesk-auth-login")
print("=" * 80)

all_passed = True

# Test 1: Missing fields
p, _ = test("Missing fields", {"httpMethod": "POST", "body": json.dumps({"email": "x@x.com"})}, 400)
all_passed &= p

# Test 2: Wrong email
p, _ = test("Wrong email", {"httpMethod": "POST", "body": json.dumps({"email": "wrong@x.com", "password": "Admin@1234"})}, 401)
all_passed &= p

# Test 3: Wrong password
p, _ = test("Wrong password", {"httpMethod": "POST", "body": json.dumps({"email": "admin@omnidesk.test", "password": "wrongpass"})}, 401)
all_passed &= p

# Test 4: Successful login
p, body = test("Valid login", {"httpMethod": "POST", "body": json.dumps({"email": "admin@omnidesk.test", "password": "Admin@1234"})}, 200)
all_passed &= p

# Test 5: Verify JWT token
if body.get("access_token"):
    payload = verify_token(body["access_token"])
    if payload and payload.get("email") == "admin@omnidesk.test" and payload.get("role") == "admin":
        print(f"PASS | JWT decode | claims: user_id={payload['user_id']}, role={payload['role']}")
    else:
        print(f"FAIL | JWT decode | payload={payload}")
        all_passed = False

print("=" * 80)
print(f"Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
