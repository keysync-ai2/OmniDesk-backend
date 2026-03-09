"""Lambda: omnidesk-order-list
GET /api/orders         — list orders (paginated, filterable by status/date)
GET /api/orders/{id}    — get single order with items
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    order_id = path_params.get("id")

    if order_id:
        return _get_single(order_id)
    return _list_orders(event)


def _get_single(order_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT o.id, o.order_number, o.customer_name, o.customer_email, o.customer_phone,
                      o.status, o.subtotal, o.tax_amount, o.discount_amount, o.total_amount,
                      o.notes, o.created_by, o.created_at, o.updated_at
               FROM orders o WHERE o.id = %s""",
            (order_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Order not found", 404)

        # Get order items with product details
        cur.execute(
            """SELECT oi.id, oi.product_id, p.name, p.sku, oi.quantity, oi.unit_price, oi.total_price
               FROM order_items oi JOIN products p ON oi.product_id = p.id
               WHERE oi.order_id = %s""",
            (order_id,),
        )
        items = [
            {
                "id": str(r[0]), "product_id": str(r[1]), "product_name": r[2],
                "sku": r[3], "quantity": r[4], "unit_price": str(r[5]), "total_price": str(r[6]),
            }
            for r in cur.fetchall()
        ]

        return success({
            "id": str(row[0]), "order_number": row[1],
            "customer_name": row[2], "customer_email": row[3], "customer_phone": row[4],
            "status": row[5], "subtotal": str(row[6]),
            "tax_amount": str(row[7]), "discount_amount": str(row[8]),
            "total_amount": str(row[9]), "notes": row[10],
            "created_by": str(row[11]), "created_at": str(row[12]), "updated_at": str(row[13]),
            "items": items,
        })
    finally:
        conn.close()


def _list_orders(event):
    qsp = event.get("queryStringParameters") or {}
    page = max(int(qsp.get("page", 1)), 1)
    limit = min(max(int(qsp.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    status_filter = (qsp.get("status") or "").strip().lower() or None
    from_date = qsp.get("from_date")
    to_date = qsp.get("to_date")
    search = (qsp.get("search") or "").strip()

    conditions = []
    params = []

    if status_filter:
        conditions.append("o.status = %s")
        params.append(status_filter)
    if from_date:
        conditions.append("o.created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("o.created_at <= %s")
        params.append(to_date)
    if search:
        conditions.append("(o.customer_name ILIKE %s OR o.order_number ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM orders o {where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"""SELECT o.id, o.order_number, o.customer_name, o.customer_email,
                       o.status, o.total_amount, o.created_at
                FROM orders o {where}
                ORDER BY o.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()

        return success({
            "orders": [
                {
                    "id": str(r[0]), "order_number": r[1], "customer_name": r[2],
                    "customer_email": r[3], "status": r[4],
                    "total_amount": str(r[5]), "created_at": str(r[6]),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if total > 0 else 1,
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
