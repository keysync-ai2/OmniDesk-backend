"""Lambda: omnidesk-order-history
GET /api/orders/{id}/history — full status change history for an order
"""
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    order_id = path_params.get("id")
    if not order_id:
        return error("Order ID is required", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Verify order exists
        cur.execute("SELECT id, order_number, status FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if not order:
            return error("Order not found", 404)

        cur.execute(
            """SELECT h.id, h.from_status, h.to_status, h.changed_by, u.full_name, h.created_at
               FROM order_status_history h
               LEFT JOIN users u ON h.changed_by = u.id
               WHERE h.order_id = %s
               ORDER BY h.created_at ASC""",
            (order_id,),
        )
        rows = cur.fetchall()

        return success({
            "order_id": str(order[0]),
            "order_number": order[1],
            "current_status": order[2],
            "history": [
                {
                    "id": str(r[0]),
                    "from_status": r[1],
                    "to_status": r[2],
                    "changed_by": str(r[3]) if r[3] else None,
                    "changed_by_name": r[4],
                    "created_at": str(r[5]),
                }
                for r in rows
            ],
            "total_transitions": len(rows),
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
