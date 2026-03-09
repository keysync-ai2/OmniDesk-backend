"""Lambda: omnidesk-invoice-generate
POST /api/invoices/generate
Input:  {order_id, tax_rate?, due_date?, notes?, currency_symbol?, tax_label?, payment_terms?}
Output: Invoice row created with tax calculation, PDF invoice uploaded to S3

Reads org_settings for company info, currency, tax defaults.
Per-invoice overrides supported via request body.
"""
import json
import os
import random
import string
from datetime import datetime, timezone, timedelta
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.pdf_builder import build_invoice_pdf


def _generate_invoice_number():
    """Generate invoice number like INV-20260309-B4K2."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"INV-{date_part}-{rand_part}"


def _get_org_settings(cur):
    """Load org_settings as a flat dict."""
    cur.execute("SELECT setting_key, setting_value FROM org_settings")
    return {r[0]: r[1] for r in cur.fetchall()}


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    order_id = body.get("order_id")
    tax_rate = body.get("tax_rate", 0)
    due_date = body.get("due_date")
    notes = (body.get("notes") or "").strip() or None

    if not order_id:
        return error("order_id is required", 400)

    try:
        tax_rate = float(tax_rate)
        if tax_rate < 0 or tax_rate > 100:
            raise ValueError
    except (ValueError, TypeError):
        return error("tax_rate must be a number between 0 and 100", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Load org settings
        settings = _get_org_settings(cur)

        # Per-invoice overrides
        for key in ("currency_symbol", "tax_label", "payment_terms", "invoice_footer"):
            if body.get(key):
                settings[key] = body[key]

        # Get order
        cur.execute(
            """SELECT id, order_number, customer_name, customer_email, customer_phone,
                      status, subtotal, total_amount
               FROM orders WHERE id = %s""",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return error("Order not found", 404)

        order_data = {
            "order_number": order[1], "customer_name": order[2],
            "customer_email": order[3], "customer_phone": order[4],
            "status": order[5],
        }

        # Check if invoice already exists for this order
        cur.execute("SELECT id, invoice_number FROM invoices WHERE order_id = %s", (order_id,))
        existing = cur.fetchone()
        if existing:
            return error(f"Invoice {existing[1]} already exists for this order", 409)

        # Get order items
        cur.execute(
            """SELECT oi.product_id, p.name, p.sku, oi.quantity, oi.unit_price, oi.total_price
               FROM order_items oi JOIN products p ON oi.product_id = p.id
               WHERE oi.order_id = %s""",
            (order_id,),
        )
        items = [
            {"product_id": str(r[0]), "product_name": r[1], "sku": r[2],
             "quantity": r[3], "unit_price": float(r[4]), "total_price": float(r[5])}
            for r in cur.fetchall()
        ]
        if not items:
            return error("Order has no items", 400)

        # Calculate totals
        subtotal = sum(item["total_price"] for item in items)
        tax_amount = round(subtotal * tax_rate / 100, 2)
        total_amount = round(subtotal + tax_amount, 2)

        # Generate invoice number
        invoice_number = _generate_invoice_number()
        for _ in range(5):
            cur.execute("SELECT id FROM invoices WHERE invoice_number = %s", (invoice_number,))
            if not cur.fetchone():
                break
            invoice_number = _generate_invoice_number()

        # Default due date: 30 days from now
        if not due_date:
            due_date_obj = datetime.now(timezone.utc) + timedelta(days=30)
            due_date = due_date_obj.strftime("%Y-%m-%d")

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        invoice_data = {
            "invoice_number": invoice_number,
            "subtotal": subtotal,
            "tax_rate": tax_rate,
            "tax_amount": tax_amount,
            "total_amount": total_amount,
            "due_date": due_date,
            "created_at": created_at,
            "notes": notes,
        }

        # Build PDF invoice
        pdf_bytes = build_invoice_pdf(invoice_data, items, order_data, settings)

        # Upload to S3
        s3_key = f"invoices/{invoice_number}.pdf"
        s3_bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(
            Bucket=s3_bucket,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
        )

        # Insert invoice row
        cur.execute(
            """INSERT INTO invoices (invoice_number, order_id, pdf_s3_key, subtotal, tax_rate,
                   tax_amount, total_amount, payment_status, status, due_date, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'unpaid', 'generated', %s, %s)
               RETURNING id, created_at""",
            (invoice_number, order_id, s3_key, subtotal, tax_rate,
             tax_amount, total_amount, due_date, user["user_id"]),
        )
        inv_row = cur.fetchone()
        conn.commit()

        invoice_id = str(inv_row[0])
        log_action(user["user_id"], "generate_invoice", "invoices", entity_id=invoice_id,
                   details={"invoice_number": invoice_number, "order_number": order_data["order_number"],
                            "total": str(total_amount), "tax_rate": str(tax_rate)})

        return success({
            "id": invoice_id,
            "invoice_number": invoice_number,
            "order_id": str(order_id),
            "order_number": order_data["order_number"],
            "customer_name": order_data["customer_name"],
            "subtotal": str(subtotal),
            "tax_rate": str(tax_rate),
            "tax_amount": str(tax_amount),
            "total_amount": str(total_amount),
            "currency": settings.get("currency_symbol", "₹"),
            "tax_label": settings.get("tax_label", "GST"),
            "payment_status": "unpaid",
            "status": "generated",
            "format": "pdf",
            "s3_key": s3_key,
            "due_date": due_date,
            "created_at": str(inv_row[1]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to generate invoice: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
