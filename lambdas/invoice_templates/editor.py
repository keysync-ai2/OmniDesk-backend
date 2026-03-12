"""Lambda: omnidesk-invoice-template-editor
GET /api/invoice-templates/editor → Generate HTML editor page → S3 → return signed URL
"""
import json
import os
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.cloudfront_signer import generate_signed_url
from utils.invoice_template_builder import build_invoice_template_editor


def _handler(event, context):
    user = event["user"]
    api_base = os.environ.get("API_BASE_URL", "https://api.omnidesk.ai")

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Load default template
        cur.execute(
            """SELECT id, config, logo_s3_key
               FROM invoice_templates WHERE is_default = TRUE LIMIT 1"""
        )
        row = cur.fetchone()

        if row:
            template_id = str(row[0])
            config = row[1] if isinstance(row[1], dict) else json.loads(row[1])
            logo_s3_key = row[2]
        else:
            template_id = "new"
            config = {
                "fields": {
                    "company_logo": True, "company_name": True, "brand_name": False,
                    "company_address": True, "company_phone": True, "company_email": True,
                    "tagline": False, "invoice_number": True, "invoice_date": True,
                    "due_date": True, "order_reference": True, "customer_name": True,
                    "customer_email": True, "customer_phone": True, "customer_address": False,
                    "item_number": True, "item_sku": True, "item_description": True,
                    "item_quantity": True, "item_unit_price": True, "item_line_total": True,
                    "subtotal": True, "tax_line": True, "grand_total": True,
                    "payment_terms": True, "notes": True, "footer_text": True,
                    "powered_by_omnidesk": True,
                },
                "custom_text": {
                    "brand_name": "", "tagline": "", "invoice_prefix": "INV", "footer_text": "",
                },
                "theme": "professional_blue",
            }
            logo_s3_key = None

        # Generate logo signed URL if exists
        logo_url = None
        if logo_s3_key:
            try:
                logo_url = generate_signed_url(logo_s3_key, expires_in=3600)
            except Exception:
                pass

        # Build HTML editor
        save_endpoint = f"{api_base}/api/invoice-templates"
        logo_endpoint = f"{api_base}/api/invoice-templates/logo"

        html = build_invoice_template_editor(config, logo_url, save_endpoint, logo_endpoint)

        # Upload to S3
        s3_key = f"invoice-templates/editor-{template_id}.html"
        s3_bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
        )

        # Generate signed URL (1 hour expiry)
        editor_url = generate_signed_url(s3_key, expires_in=3600)

        return success({
            "editor_url": editor_url,
            "template_id": template_id,
            "expires_in": "1 hour",
            "message": "Open this URL in your browser to customize your invoice template.",
        })
    except Exception as e:
        return error(f"Failed to generate editor: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="admin")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
