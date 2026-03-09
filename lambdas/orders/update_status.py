"""Lambda: omnidesk-order-status
PATCH /api/orders/{id}/status  — transition order status with validation
PATCH /api/orders/{id}/cancel  — cancel order (admin, with confirmation gate)

Status flow: pending → confirmed → shipped → delivered
              pending → cancelled (admin only, via cancel endpoint)
              confirmed → cancelled (admin only, restores stock)

On confirmed: stock is deducted from specified warehouse (or default first warehouse).
On cancelled (from confirmed): stock is restored.
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action

VALID_TRANSITIONS = {
    "pending": ["confirmed", "cancelled"],
    "confirmed": ["shipped", "cancelled"],
    "shipped": ["delivered"],
    "delivered": [],
    "cancelled": [],
}


def _status_handler(event, context):
    """PATCH /api/orders/{id}/status — standard status transitions."""
    path_params = event.get("pathParameters") or {}
    order_id = path_params.get("id")
    if not order_id:
        return error("Order ID is required", 400)

    body = json.loads(event.get("body") or "{}")
    user = event["user"]
    new_status = (body.get("status") or "").strip().lower()
    warehouse_id = body.get("warehouse_id")  # needed for stock deduction on confirm

    if not new_status:
        return error("status is required", 400)

    # Cancel must go through cancel endpoint
    if new_status == "cancelled":
        return error("Use the cancel endpoint to cancel orders", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Get current order
        cur.execute("SELECT id, status, order_number FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if not order:
            return error("Order not found", 404)

        current_status = order[1]
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            return error(
                f"Cannot transition from '{current_status}' to '{new_status}'. "
                f"Allowed transitions: {', '.join(allowed) if allowed else 'none (terminal state)'}",
                400,
            )

        # On confirmed: deduct stock
        if new_status == "confirmed":
            _deduct_stock_for_order(cur, order_id, warehouse_id, user["user_id"])

        # Update status
        cur.execute(
            "UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s",
            (new_status, order_id),
        )

        # Record history
        cur.execute(
            """INSERT INTO order_status_history (order_id, from_status, to_status, changed_by)
               VALUES (%s, %s, %s, %s) RETURNING id, created_at""",
            (order_id, current_status, new_status, user["user_id"]),
        )
        history_row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "update_order_status", "orders", entity_id=order_id,
                   details={"order_number": order[2], "from": current_status, "to": new_status})

        return success({
            "order_id": str(order[0]),
            "order_number": order[2],
            "previous_status": current_status,
            "new_status": new_status,
            "history_id": str(history_row[0]),
            "changed_at": str(history_row[1]),
        })
    except Exception as e:
        conn.rollback()
        return error(f"Failed to update order status: {str(e)}", 500)
    finally:
        conn.close()


def _cancel_handler(event, context):
    """PATCH /api/orders/{id}/cancel — cancel with confirmation gate (admin only)."""
    path_params = event.get("pathParameters") or {}
    order_id = path_params.get("id")
    if not order_id:
        return error("Order ID is required", 400)

    body = json.loads(event.get("body") or "{}")
    user = event["user"]
    confirmed = body.get("confirm", False)

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT id, status, order_number, customer_name, total_amount FROM orders WHERE id = %s",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return error("Order not found", 404)

        current_status = order[1]
        if current_status == "cancelled":
            return error("Order is already cancelled", 400)
        if current_status == "delivered":
            return error("Cannot cancel a delivered order. Use returns instead.", 400)

        # Confirmation gate
        if not confirmed:
            msg = f"Are you sure you want to cancel Order {order[2]} ({order[3]}, total ₹{order[4]})?"
            if current_status == "confirmed":
                msg += " Stock that was deducted will be restored."
            return success({
                "confirmation_required": True,
                "message": msg,
                "order_id": str(order[0]),
                "order_number": order[2],
                "current_status": current_status,
                "instruction": "Call again with confirm: true to proceed.",
            })

        # Restore stock if was confirmed
        if current_status == "confirmed":
            _restore_stock_for_order(cur, order_id, user["user_id"])

        # Cancel
        cur.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = NOW() WHERE id = %s",
            (order_id,),
        )
        cur.execute(
            """INSERT INTO order_status_history (order_id, from_status, to_status, changed_by)
               VALUES (%s, %s, 'cancelled', %s) RETURNING id, created_at""",
            (order_id, current_status, user["user_id"]),
        )
        history_row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "cancel_order", "orders", entity_id=order_id,
                   details={"order_number": order[2], "from": current_status,
                            "stock_restored": current_status == "confirmed"})

        result = {
            "order_id": str(order[0]),
            "order_number": order[2],
            "previous_status": current_status,
            "new_status": "cancelled",
            "changed_at": str(history_row[1]),
        }
        if current_status == "confirmed":
            result["stock_restored"] = True
        return success(result)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to cancel order: {str(e)}", 500)
    finally:
        conn.close()


def _deduct_stock_for_order(cur, order_id, warehouse_id, user_id):
    """Deduct stock for all items in the order."""
    cur.execute(
        "SELECT oi.product_id, oi.quantity, p.name FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s",
        (order_id,),
    )
    items = cur.fetchall()

    # If no warehouse specified, use the first active warehouse
    if not warehouse_id:
        cur.execute("SELECT id FROM warehouses WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
        wh = cur.fetchone()
        if not wh:
            raise ValueError("No active warehouse found. Create a warehouse first.")
        warehouse_id = str(wh[0])

    for product_id, qty, product_name in items:
        # Get current stock
        cur.execute(
            "SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s",
            (str(product_id), warehouse_id),
        )
        stock_row = cur.fetchone()
        current_qty = stock_row[1] if stock_row else 0

        if current_qty < qty:
            raise ValueError(f"Insufficient stock for {product_name}: have {current_qty}, need {qty}")

        new_qty = current_qty - qty
        if stock_row:
            cur.execute("UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute(
                "INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)",
                (str(product_id), warehouse_id, new_qty),
            )

        # Record movement
        cur.execute(
            """INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by)
               VALUES (%s, %s, 'deduct', %s, %s, %s)""",
            (str(product_id), warehouse_id, qty, f"Order confirmed (order_id: {order_id})", user_id),
        )


def _restore_stock_for_order(cur, order_id, user_id):
    """Restore stock for all items when order is cancelled from confirmed state."""
    cur.execute(
        "SELECT oi.product_id, oi.quantity FROM order_items oi WHERE oi.order_id = %s",
        (order_id,),
    )
    items = cur.fetchall()

    # Find warehouse from the deduction movements for this order
    cur.execute(
        """SELECT DISTINCT warehouse_id FROM stock_movements
           WHERE reason LIKE %s AND movement_type = 'deduct' AND warehouse_id IS NOT NULL LIMIT 1""",
        (f"%{order_id}%",),
    )
    wh_row = cur.fetchone()
    if not wh_row:
        cur.execute("SELECT id FROM warehouses WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
        wh_row = cur.fetchone()
    warehouse_id = str(wh_row[0]) if wh_row else None

    if not warehouse_id:
        return

    for product_id, qty in items:
        cur.execute(
            "SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s",
            (str(product_id), warehouse_id),
        )
        stock_row = cur.fetchone()
        current_qty = stock_row[1] if stock_row else 0
        new_qty = current_qty + qty

        if stock_row:
            cur.execute("UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute(
                "INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)",
                (str(product_id), warehouse_id, new_qty),
            )

        cur.execute(
            """INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by)
               VALUES (%s, %s, 'add', %s, %s, %s)""",
            (str(product_id), warehouse_id, qty, f"Order cancelled - stock restored (order_id: {order_id})", user_id),
        )


status_handler = require_auth(_status_handler, min_role="staff")
cancel_handler = require_auth(_cancel_handler, min_role="admin")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)

    path = event.get("path") or ""
    if path.endswith("/cancel"):
        return cancel_handler(event, context)
    return status_handler(event, context)
