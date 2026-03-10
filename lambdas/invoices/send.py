"""Lambda: omnidesk-invoice-send
POST /api/invoices/{id}/send
Sends invoice to customer. SES is deferred — for now returns a download link
and marks the invoice as 'sent'. When SES is ready, this will email the link.
"""
from datetime import datetime, timezone
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.cloudfront_signer import generate_signed_url


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    invoice_id = path_params.get("id")
    if not invoice_id:
        return error("Invoice ID is required", 400)

    user = event["user"]
    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(
            """SELECT i.id, i.invoice_number, i.pdf_s3_key, i.sent_at, i.order_id,
                      o.customer_name, o.customer_email
               FROM invoices i JOIN orders o ON i.order_id = o.id
               WHERE i.id = %s""",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Invoice not found", 404)

        s3_key = row[2]
        if not s3_key:
            return error("Invoice file not found. Generate the invoice first.", 400)

        customer_email = row[6]

        # Generate download link (24h for email delivery window)
        download_url = generate_signed_url(s3_key, expires_in=86400)

        # Mark as sent
        now = datetime.now(timezone.utc)
        cur.execute(
            "UPDATE invoices SET sent_at = %s, status = 'sent' WHERE id = %s",
            (now, invoice_id),
        )
        conn.commit()

        log_action(user["user_id"], "send_invoice", "invoices", entity_id=invoice_id,
                   details={"invoice_number": row[1], "customer_email": customer_email})

        result = {
            "invoice_id": str(row[0]),
            "invoice_number": row[1],
            "customer_name": row[5],
            "download_url": download_url,
            "status": "sent",
            "sent_at": str(now),
        }

        if customer_email:
            result["customer_email"] = customer_email
            result["email_note"] = "Email delivery will be available when SES is configured (Phase 4). Share the download link with the customer for now."
        else:
            result["email_note"] = "No customer email on file. Share the download link directly."

        return success(result)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to send invoice: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
