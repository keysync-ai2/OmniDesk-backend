"""Local test for RBAC middleware."""
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

from utils.response import success
from utils.auth_middleware import require_auth
from utils.jwt_helper import create_access_token

# Create a dummy protected handler
def protected_handler(event, context):
    return success({"message": "Access granted", "user": event["user"]["email"]})

# Wrap with different role requirements
admin_only = require_auth(protected_handler, min_role="admin")
staff_up = require_auth(protected_handler, min_role="staff")
viewer_up = require_auth(protected_handler, min_role="viewer")

# Generate tokens for different roles
admin_token = create_access_token("uuid-admin", "admin@test.com", "admin")
staff_token = create_access_token("uuid-staff", "staff@test.com", "staff")
viewer_token = create_access_token("uuid-viewer", "viewer@test.com", "viewer")

def test(name, handler, token, expected_status):
    event = {"httpMethod": "GET", "headers": {"Authorization": f"Bearer {token}"}}
    result = handler(event, None)
    status = result["statusCode"]
    body = json.loads(result["body"])
    passed = status == expected_status
    print(f"{'PASS' if passed else 'FAIL'} | {name} | {status} | {json.dumps(body)[:100]}")
    return passed

print("=" * 80)
print("Testing: RBAC Middleware")
print("=" * 80)

all_passed = True
all_passed &= test("Admin on admin-only", admin_only, admin_token, 200)
all_passed &= test("Staff on admin-only", admin_only, staff_token, 403)
all_passed &= test("Viewer on admin-only", admin_only, viewer_token, 403)
all_passed &= test("Admin on staff-up", staff_up, admin_token, 200)
all_passed &= test("Staff on staff-up", staff_up, staff_token, 200)
all_passed &= test("Viewer on staff-up", staff_up, viewer_token, 403)
all_passed &= test("Viewer on viewer-up", viewer_up, viewer_token, 200)

# Test no auth header
result = admin_only({"httpMethod": "GET", "headers": {}}, None)
p = result["statusCode"] == 401
print(f"{'PASS' if p else 'FAIL'} | No auth header | {result['statusCode']}")
all_passed &= p

print("=" * 80)
print(f"Result: {'ALL PASSED' if all_passed else 'SOME FAILED'}")
