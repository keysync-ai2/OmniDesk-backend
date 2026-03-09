"""Lambda: omnidesk-order-create
POST /api/orders
Input:  {customer_name, customer_email, customer_phone, items: [{product_id, quantity}], notes}
Output: {order with line items, auto-calculated totals, auto-generated order_number}
Stock is NOT deducted at creation — only on status change to 'confirmed'.
"""
import json
import random
import string
from datetime import datetime, timezone
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action


def _generate_order_number():
    """Generate order number like ORD-20260309-A3F7."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"ORD-{date_part}-{rand_part}"


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    customer_name = (body.get("customer_name") or "").strip()
    customer_email = (body.get("customer_email") or "").strip() or None
    customer_phone = (body.get("customer_phone") or "").strip() or None
    items = body.get("items") or []
    notes = (body.get("notes") or "").strip() or None

    # Validation
    if not customer_name:
        return error("customer_name is required", 400)
    if not items or not isinstance(items, list):
        return error("items is required and must be a non-empty array of {product_id, quantity}", 400)

    # Validate each item
    for i, item in enumerate(items):
        if not item.get("product_id"):
            return error(f"items[{i}].product_id is required", 400)
        try:
            qty = int(item.get("quantity", 0))
            if qty <= 0:
                raise ValueError
            item["quantity"] = qty
        except (ValueError, TypeError):
            return error(f"items[{i}].quantity must be a positive integer", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Fetch product prices and validate all products exist
        product_ids = [item["product_id"] for item in items]
        placeholders = ",".join(["%s"] * len(product_ids))
        cur.execute(
            f"SELECT id, name, sku, unit_price FROM products WHERE id IN ({placeholders}) AND is_active = TRUE",
            product_ids,
        )
        products = {str(r[0]): {"name": r[1], "sku": r[2], "unit_price": r[3]} for r in cur.fetchall()}

        # Check all products found
        missing = [pid for pid in product_ids if pid not in products]
        if missing:
            return error(f"Products not found: {', '.join(missing)}", 404)

        # Generate unique order number (retry on collision)
        order_number = _generate_order_number()
        for _ in range(5):
            cur.execute("SELECT id FROM orders WHERE order_number = %s", (order_number,))
            if not cur.fetchone():
                break
            order_number = _generate_order_number()

        # Calculate totals
        subtotal = 0
        order_items_data = []
        for item in items:
            product = products[item["product_id"]]
            item_total = float(product["unit_price"]) * item["quantity"]
            subtotal += item_total
            order_items_data.append({
                "product_id": item["product_id"],
                "product_name": product["name"],
                "sku": product["sku"],
                "quantity": item["quantity"],
                "unit_price": float(product["unit_price"]),
                "total_price": item_total,
            })

        total_amount = subtotal  # tax/discount applied at invoice stage

        # Insert order
        cur.execute(
            """INSERT INTO orders (order_number, customer_name, customer_email, customer_phone,
                   status, subtotal, total_amount, notes, created_by)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s)
               RETURNING id, order_number, status, created_at""",
            (order_number, customer_name, customer_email, customer_phone,
             subtotal, total_amount, notes, user["user_id"]),
        )
        order_row = cur.fetchone()
        order_id = str(order_row[0])

        # Insert order items
        result_items = []
        for oi in order_items_data:
            cur.execute(
                """INSERT INTO order_items (order_id, product_id, quantity, unit_price, total_price)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id""",
                (order_id, oi["product_id"], oi["quantity"], oi["unit_price"], oi["total_price"]),
            )
            item_row = cur.fetchone()
            result_items.append({
                "id": str(item_row[0]),
                "product_id": oi["product_id"],
                "product_name": oi["product_name"],
                "sku": oi["sku"],
                "quantity": oi["quantity"],
                "unit_price": oi["unit_price"],
                "total_price": oi["total_price"],
            })

        # Insert initial status history
        cur.execute(
            """INSERT INTO order_status_history (order_id, from_status, to_status, changed_by)
               VALUES (%s, NULL, 'pending', %s)""",
            (order_id, user["user_id"]),
        )

        conn.commit()

        log_action(user["user_id"], "create_order", "orders", entity_id=order_id,
                   details={"order_number": order_row[1], "customer": customer_name,
                            "items_count": len(items), "total": str(total_amount)})

        return success({
            "id": order_id,
            "order_number": order_row[1],
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "status": order_row[2],
            "items": result_items,
            "subtotal": str(subtotal),
            "total_amount": str(total_amount),
            "notes": notes,
            "created_by": user["user_id"],
            "created_at": str(order_row[3]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to create order: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="staff")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
