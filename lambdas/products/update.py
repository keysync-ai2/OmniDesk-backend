"""Lambda: omnidesk-product-update
PUT   /api/products/{id}              — Update product fields
PATCH /api/products/{id}/deactivate   — Soft-delete product
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.pinecone_helper import upsert_product, delete_product


def _update(event, context):
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("id")
    if not product_id:
        return error("Product ID is required", 400)

    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    # Build dynamic SET clause
    allowed = {"name": str, "description": str, "category_id": str, "unit_price": float, "unit": str}
    updates = []
    params = []
    for field, cast in allowed.items():
        if field in body:
            val = body[field]
            if val is not None:
                try:
                    val = cast(val) if val != "" else None
                except (ValueError, TypeError):
                    return error(f"Invalid value for {field}", 400)
            updates.append(f"{field} = %s")
            params.append(val)

    if not updates:
        return error("No fields to update", 400)

    updates.append("updated_at = NOW()")
    params.append(product_id)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Check product exists and is active
        cur.execute("SELECT is_active FROM products WHERE id = %s", (product_id,))
        row = cur.fetchone()
        if not row:
            return error("Product not found", 404)
        if not row[0]:
            return error("Product has been deactivated", 400)

        # Validate category if being changed
        if "category_id" in body and body["category_id"]:
            cur.execute("SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (body["category_id"],))
            if not cur.fetchone():
                return error("Category not found", 404)

        set_clause = ", ".join(updates)
        cur.execute(
            f"""
            UPDATE products SET {set_clause} WHERE id = %s AND is_active = TRUE
            RETURNING id, sku, name, description, category_id, unit_price, unit, updated_at
            """,
            params,
        )
        updated = cur.fetchone()
        if not updated:
            return error("Product not found or deactivated", 404)
        conn.commit()

        log_action(user["user_id"], "update_product", "products", entity_id=product_id,
                   details={k: str(body[k]) for k in body if k in allowed})

        # Re-index in Pinecone with updated data (best-effort)
        upsert_product(product_id, name=updated[2], description=updated[3],
                       sku=updated[1], unit=updated[6], unit_price=str(updated[5]))

        return success({
            "id": str(updated[0]),
            "sku": updated[1],
            "name": updated[2],
            "description": updated[3],
            "category_id": str(updated[4]) if updated[4] else None,
            "unit_price": str(updated[5]),
            "unit": updated[6],
            "updated_at": str(updated[7]),
        })
    except Exception as e:
        conn.rollback()
        return error(f"Failed to update product: {str(e)}", 500)
    finally:
        conn.close()


def _deactivate(event, context):
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("id")
    if not product_id:
        return error("Product ID is required", 400)

    user = event["user"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE products SET is_active = FALSE, updated_at = NOW() WHERE id = %s AND is_active = TRUE RETURNING id, name",
            (product_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Product not found or already deactivated", 404)
        conn.commit()

        log_action(user["user_id"], "deactivate_product", "products", entity_id=product_id,
                   details={"name": row[1]})

        # Remove from Pinecone index (best-effort)
        delete_product(product_id)

        return success({"message": f"Product '{row[1]}' deactivated", "id": str(row[0])})
    except Exception as e:
        conn.rollback()
        return error(f"Failed to deactivate product: {str(e)}", 500)
    finally:
        conn.close()


update_handler = require_auth(_update, min_role="manager")
deactivate_handler = require_auth(_deactivate, min_role="admin")


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)

    path = event.get("path", "")
    if method == "PATCH" and path.endswith("/deactivate"):
        return deactivate_handler(event, context)
    if method == "PUT":
        return update_handler(event, context)

    return error("Method not allowed", 405)
