"""File download Lambda — validates JWT, generates CloudFront signed URL, returns 302 redirect.

GET /api/files/download?id={resource_id}&type={report|invoice|form|submission}
"""
import json
from utils.response import error, CORS_HEADERS
from utils.auth_middleware import require_auth
from utils.db import get_connection
from utils.cloudfront_signer import generate_signed_url
from utils.audit import log_action

# Map resource type to DB lookup
RESOURCE_CONFIG = {
    "report": {
        "table": "reports",
        "s3_key_col": "s3_key",
        "min_role": "viewer",
        "expires_in": 300,  # 5 min
    },
    "invoice": {
        "table": "invoices",
        "s3_key_col": "pdf_s3_key",
        "min_role": "viewer",
        "expires_in": 300,
    },
    "form": {
        "table": "forms",
        "s3_key_col": "s3_key",
        "min_role": "viewer",
        "expires_in": 86400,  # 24h for public forms
    },
    "submission": {
        "table": "form_submissions",
        "s3_key_col": "s3_key",
        "min_role": "manager",
        "expires_in": 300,
    },
}


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    params = event.get("queryStringParameters") or {}
    resource_id = params.get("id")
    resource_type = params.get("type", "").lower()

    if not resource_id:
        return error("Missing 'id' query parameter", 400)

    if resource_type not in RESOURCE_CONFIG:
        return error(f"Invalid type. Must be one of: {', '.join(RESOURCE_CONFIG.keys())}", 400)

    config = RESOURCE_CONFIG[resource_type]
    user = event.get("user", {})

    # Look up the S3 key from the database
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {config['s3_key_col']} FROM {config['table']} WHERE id = %s",
            (resource_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return error(f"{resource_type.title()} not found or has no file", 404)

        s3_key = row[0]
    finally:
        conn.close()

    # Generate CloudFront signed URL and redirect
    signed_url = generate_signed_url(s3_key, expires_in=config["expires_in"])

    # Audit log
    log_action(
        user_id=user.get("user_id", "anonymous"),
        action="file_download",
        module=resource_type,
        entity_id=resource_id,
        details={"s3_key": s3_key},
    )

    return {
        "statusCode": 302,
        "headers": {
            **CORS_HEADERS,
            "Location": signed_url,
            "Cache-Control": "no-store",
        },
        "body": "",
    }


# Auth wrapper — viewer minimum (individual resource types may enforce higher via RBAC in the query)
lambda_handler = require_auth(lambda_handler, min_role="viewer")
