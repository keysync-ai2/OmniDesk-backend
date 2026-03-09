"""Local test for auth register Lambda."""
import sys
import os
import json

# Add backend to path so utils imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set DB connection for local testing
os.environ["DATABASE_URL"] = (
    "postgresql://neondb_owner:npg_rmhlnp2twyM8"
    "@ep-aged-frost-ad88w6kz-pooler.c-2.us-east-1.aws.neon.tech"
    "/neondb?sslmode=require"
)

from lambdas.auth.register import lambda_handler

def test(name, event, expected_status):
    result = lambda_handler(event, None)
    status = result["statusCode"]
    body = json.loads(result["body"]) if result.get("body") else {}
    passed = status == expected_status
    print(f"{'PASS' if passed else 'FAIL'} | {name} | {status} | {json.dumps(body, default=str)[:120]}")
    return passed

print("=" * 80)
print("Testing: omnidesk-auth-register")
print("=" * 80)

all_passed = True

# Test 1: Missing fields
all_passed &= test("Missing email", {"httpMethod": "POST", "body": json.dumps({"password": "12345678", "full_name": "Test"})}, 400)

# Test 2: Short password
all_passed &= test("Short password", {"httpMethod": "POST", "body": json.dumps({"email": "x@x.com", "password": "123", "full_name": "Test"})}, 400)

# Test 3: Invalid role
all_passed &= test("Invalid role", {"httpMethod": "POST", "body": json.dumps({"email": "x@x.com", "password": "12345678", "full_name": "Test", "role": "superuser"})}, 400)

# Test 4: Successful registration
all_passed &= test("Register admin", {"httpMethod": "POST", "body": json.dumps({
    "email": "admin@omnidesk.test",
    "password": "Admin@1234",
    "full_name": "OmniDesk Admin",
    "phone": "+919876543210",
    "role": "admin"
})}, 201)

# Test 5: Duplicate email
all_passed &= test("Duplicate email", {"httpMethod": "POST", "body": json.dumps({
    "email": "admin@omnidesk.test",
    "password": "Admin@1234",
    "full_name": "Duplicate User"
})}, 409)

# Test 6: OPTIONS preflight
all_passed &= test("CORS preflight", {"httpMethod": "OPTIONS"}, 204)

print("=" * 80)
print(f"Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
