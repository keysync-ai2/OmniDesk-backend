"""JWT token creation and verification."""
import json
import os
from datetime import datetime, timedelta, timezone
import boto3
import jwt

_cached_secret = None

ACCESS_TOKEN_EXPIRY_HOURS = 48
REFRESH_TOKEN_EXPIRY_DAYS = 30


def get_jwt_secret():
    """Get JWT secret from Secrets Manager (cached per Lambda container)."""
    global _cached_secret
    if _cached_secret:
        return _cached_secret

    # Allow override via env var for local testing
    secret = os.environ.get("JWT_SECRET")
    if secret:
        _cached_secret = secret
        return secret

    secret_arn = os.environ.get("JWT_SECRET_ARN", "omnidesk/jwt-secret")
    client = boto3.client("secretsmanager", region_name="us-east-1")
    data = json.loads(
        client.get_secret_value(SecretId=secret_arn)["SecretString"]
    )
    _cached_secret = data["secret"]
    return _cached_secret


def create_access_token(user_id, email, role):
    """Create a JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": str(user_id),
        "email": email,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=ACCESS_TOKEN_EXPIRY_HOURS),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def create_refresh_token(user_id):
    """Create a JWT refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def verify_token(token, expected_type="access"):
    """Verify and decode a JWT token. Returns payload dict or None."""
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
        if payload.get("type") != expected_type:
            return None
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
