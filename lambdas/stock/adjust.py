"""Lambda: omnidesk-stock-adjust
POST /api/stock/adjust
Input:  {product_id, warehouse_id, movement_type: add/deduct/adjust, quantity, reason}
Output: {stock level after adjustment, movement record}
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action

VALID_MOVEMENT_TYPES = {"add", "deduct", "adjust"}


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    product_id = body.get("product_id")
    warehouse_id = body.get("warehouse_id")
    movement_type = (body.get("movement_type") or "").strip().lower()
    quantity = body.get("quantity")
    reason = (body.get("reason") or "").strip() or None

    # Validation
    if not product_id:
        return error("product_id is required", 400)
    if not warehouse_id:
        return error("warehouse_id is required", 400)
    if movement_type not in VALID_MOVEMENT_TYPES:
        return error(f"movement_type must be one of: {', '.join(sorted(VALID_MOVEMENT_TYPES))}", 400)
    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return error("quantity must be a positive integer", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Verify product exists
        cur.execute("SELECT id, name FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
        product = cur.fetchone()
        if not product:
            return error("Product not found", 404)

        # Verify warehouse exists
        cur.execute("SELECT id, name FROM warehouses WHERE id = %s AND is_active = TRUE", (warehouse_id,))
        warehouse = cur.fetchone()
        if not warehouse:
            return error("Warehouse not found", 404)

        # Get or create stock row (upsert)
        cur.execute(
            "SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s",
            (product_id, warehouse_id),
        )
        stock_row = cur.fetchone()

        if stock_row:
            current_qty = stock_row[1]
        else:
            current_qty = 0

        # Calculate new quantity
        if movement_type == "add":
            new_qty = current_qty + quantity
        elif movement_type == "deduct":
            new_qty = current_qty - quantity
            if new_qty < 0:
                return error(f"Insufficient stock. Current: {current_qty}, Requested deduction: {quantity}", 400)
        else:  # adjust (set absolute)
            new_qty = quantity

        # Upsert stock
        if stock_row:
            cur.execute(
                "UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s RETURNING id",
                (new_qty, stock_row[0]),
            )
        else:
            cur.execute(
                "INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s) RETURNING id",
                (product_id, warehouse_id, new_qty),
            )

        # Record movement
        cur.execute(
            """
            INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
            """,
            (product_id, warehouse_id, movement_type, quantity, reason, user["user_id"]),
        )
        movement = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], f"stock_{movement_type}", "stock", entity_id=product_id,
                   details={"warehouse_id": warehouse_id, "quantity": quantity, "new_total": new_qty, "reason": reason})

        return success({
            "product_id": str(product[0]),
            "product_name": product[1],
            "warehouse_id": str(warehouse[0]),
            "warehouse_name": warehouse[1],
            "movement_type": movement_type,
            "quantity_changed": quantity,
            "previous_quantity": current_qty,
            "new_quantity": new_qty,
            "movement_id": str(movement[0]),
            "reason": reason,
            "created_at": str(movement[1]),
        })
    except Exception as e:
        conn.rollback()
        if "Insufficient stock" in str(e):
            raise
        return error(f"Failed to adjust stock: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="staff")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
