"""Lambda: omnidesk-invoice-download
GET /api/invoices/{id}/download — returns a presigned S3 URL for the invoice (valid 15 min)
"""
import os
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    invoice_id = path_params.get("id")
    if not invoice_id:
        return error("Invoice ID is required", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, invoice_number, pdf_s3_key FROM invoices WHERE id = %s",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Invoice not found", 404)

        s3_key = row[2]
        if not s3_key:
            return error("Invoice file not found. Regenerate the invoice.", 400)

        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")

        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=900,  # 15 minutes
        )

        return success({
            "invoice_id": str(row[0]),
            "invoice_number": row[1],
            "download_url": url,
            "expires_in": "15 minutes",
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
