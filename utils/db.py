"""Database connection helper — reads credentials from Secrets Manager."""
import json
import os
import boto3
import psycopg2

_cached_conn_string = None


def get_connection_string():
    """Get DB connection string from Secrets Manager (cached per Lambda container)."""
    global _cached_conn_string
    if _cached_conn_string:
        return _cached_conn_string

    # Allow override via env var for local testing
    conn_str = os.environ.get("DATABASE_URL")
    if conn_str:
        _cached_conn_string = conn_str
        return conn_str

    secret_arn = os.environ.get("SECRETS_ARN", "omnidesk/db-credentials")
    client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = json.loads(
        client.get_secret_value(SecretId=secret_arn)["SecretString"]
    )

    if "connection_string" in secret:
        _cached_conn_string = secret["connection_string"]
    else:
        _cached_conn_string = (
            f"postgresql://{secret['username']}:{secret['password']}"
            f"@{secret['host']}:{secret['port']}/{secret['dbname']}?sslmode=require"
        )
    return _cached_conn_string


def get_connection():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(get_connection_string())
