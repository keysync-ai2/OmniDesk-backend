"""Lambda: omnidesk-invoice-template-logo
POST /api/invoice-templates/logo → Upload logo image (base64 → S3)
"""
import json
import os
import base64
import time
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.cloudfront_signer import generate_signed_url


ALLOWED_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
MAX_SIZE_BYTES = 2 * 1024 * 1024  # 2MB


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    data_b64 = body.get("data")
    filename = body.get("filename", "logo.png")
    content_type = body.get("content_type", "image/png")

    if not data_b64:
        return error("data (base64 encoded image) is required", 400)

    ext = ALLOWED_TYPES.get(content_type)
    if not ext:
        return error(f"Invalid content_type. Allowed: {', '.join(ALLOWED_TYPES.keys())}", 400)

    try:
        image_bytes = base64.b64decode(data_b64)
    except Exception:
        return error("Invalid base64 data", 400)

    if len(image_bytes) > MAX_SIZE_BYTES:
        return error(f"Image too large. Maximum size is {MAX_SIZE_BYTES // (1024*1024)}MB", 400)

    # Upload to S3
    timestamp = int(time.time())
    s3_key = f"logos/logo-{timestamp}.{ext}"
    s3_bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
    s3 = boto3.client("s3", region_name="us-east-1")

    try:
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=image_bytes,
            ContentType=content_type,
        )
    except Exception as e:
        return error(f"Failed to upload logo: {str(e)}", 500)

    # Update default template's logo_s3_key
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE invoice_templates SET logo_s3_key = %s, updated_at = NOW()
               WHERE is_default = TRUE""",
            (s3_key,),
        )
        if cur.rowcount == 0:
            # No default template yet — create one
            cur.execute(
                """INSERT INTO invoice_templates (name, is_default, config, logo_s3_key, created_by)
                   VALUES ('Default', TRUE, '{}', %s, %s)""",
                (s3_key, user["user_id"]),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return error(f"Failed to update template: {str(e)}", 500)
    finally:
        conn.close()

    # Return signed URL for immediate preview
    logo_url = generate_signed_url(s3_key, expires_in=3600)

    return success({
        "logo_url": logo_url,
        "s3_key": s3_key,
        "message": "Logo uploaded successfully",
    }, 201)


handler = require_auth(_handler, min_role="admin")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
