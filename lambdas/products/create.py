"""Lambda: omnidesk-product-create
POST /api/products
Input:  {sku, name, description, category_id, unit_price, unit}
Output: {id, sku, name, description, category_id, unit_price, unit, created_by, created_at}
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.pinecone_helper import upsert_product


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    sku = (body.get("sku") or "").strip().upper()
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    category_id = body.get("category_id")
    unit_price = body.get("unit_price")
    unit = (body.get("unit") or "pcs").strip().lower()

    # Validation
    if not sku:
        return error("SKU is required", 400)
    if not name:
        return error("Product name is required", 400)
    if unit_price is None:
        return error("Unit price is required", 400)
    try:
        unit_price = float(unit_price)
        if unit_price < 0:
            raise ValueError
    except (ValueError, TypeError):
        return error("Unit price must be a non-negative number", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Validate category exists if provided
        if category_id:
            cur.execute("SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (category_id,))
            if not cur.fetchone():
                return error("Category not found", 404)

        # Check duplicate SKU
        cur.execute("SELECT id FROM products WHERE sku = %s", (sku,))
        if cur.fetchone():
            return error(f"Product with SKU '{sku}' already exists", 409)

        cur.execute(
            """
            INSERT INTO products (sku, name, description, category_id, unit_price, unit, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, sku, name, description, category_id, unit_price, unit, created_by, created_at
            """,
            (sku, name, description, category_id, unit_price, unit, user["user_id"]),
        )
        row = cur.fetchone()
        conn.commit()

        product_id = str(row[0])
        log_action(user["user_id"], "create_product", "products", entity_id=product_id,
                   details={"sku": row[1], "name": row[2], "unit_price": str(row[5])})

        # Index in Pinecone for semantic search (best-effort, non-blocking)
        upsert_product(product_id, name=row[2], description=row[3],
                       sku=row[1], unit=row[6], unit_price=str(row[5]))

        return success({
            "id": product_id,
            "sku": row[1],
            "name": row[2],
            "description": row[3],
            "category_id": str(row[4]) if row[4] else None,
            "unit_price": str(row[5]),
            "unit": row[6],
            "created_by": str(row[7]),
            "created_at": str(row[8]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to create product: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
