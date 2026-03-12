"""Lambda: omnidesk-invoice-template
GET  /api/invoice-templates  → Get default template config
POST /api/invoice-templates  → Save/update template config
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action


def _get_handler(event, context):
    """Get the default invoice template."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, config, logo_s3_key, updated_at, created_at
               FROM invoice_templates WHERE is_default = TRUE LIMIT 1"""
        )
        row = cur.fetchone()
        if not row:
            return success({
                "id": None,
                "name": "Default",
                "config": {
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
                },
                "logo_s3_key": None,
            })

        config = row[2] if isinstance(row[2], dict) else json.loads(row[2])
        return success({
            "id": str(row[0]),
            "name": row[1],
            "config": config,
            "logo_s3_key": row[3],
            "updated_at": str(row[4]),
            "created_at": str(row[5]),
        })
    finally:
        conn.close()


def _post_handler(event, context):
    """Save/update the default invoice template config."""
    body = json.loads(event.get("body") or "{}")
    user = event["user"]
    config = body.get("config")

    if not config or not isinstance(config, dict):
        return error("config object is required", 400)

    # Validate theme
    valid_themes = {"professional_blue", "forest_green", "charcoal", "warm_terracotta", "royal_purple"}
    if config.get("theme") and config["theme"] not in valid_themes:
        return error(f"Invalid theme. Choose from: {', '.join(sorted(valid_themes))}", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Check if default template exists
        cur.execute("SELECT id FROM invoice_templates WHERE is_default = TRUE LIMIT 1")
        existing = cur.fetchone()

        if existing:
            cur.execute(
                """UPDATE invoice_templates SET config = %s, updated_at = NOW()
                   WHERE id = %s RETURNING id, updated_at""",
                (json.dumps(config), existing[0]),
            )
            row = cur.fetchone()
            template_id = str(row[0])
        else:
            cur.execute(
                """INSERT INTO invoice_templates (name, is_default, config, created_by)
                   VALUES ('Default', TRUE, %s, %s) RETURNING id, created_at""",
                (json.dumps(config), user["user_id"]),
            )
            row = cur.fetchone()
            template_id = str(row[0])

        conn.commit()

        log_action(user["user_id"], "update_invoice_template", "invoice_templates",
                   entity_id=template_id, details={"theme": config.get("theme", "professional_blue")})

        return success({"id": template_id, "config": config, "message": "Template saved"})
    except Exception as e:
        conn.rollback()
        return error(f"Failed to save template: {str(e)}", 500)
    finally:
        conn.close()


get_handler = require_auth(_get_handler, min_role="viewer")
post_handler = require_auth(_post_handler, min_role="admin")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    if event.get("httpMethod") == "GET":
        return get_handler(event, context)
    return post_handler(event, context)
