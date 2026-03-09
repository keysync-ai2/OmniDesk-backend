"""Lambda: omnidesk-stock-low
GET /api/stock/low               — List all low-stock products
GET /api/stock/low?warehouse_id= — Filter by warehouse
"""
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    qs = event.get("queryStringParameters") or {}
    warehouse_id = qs.get("warehouse_id")

    conn = get_connection()
    try:
        cur = conn.cursor()

        if warehouse_id:
            cur.execute(
                """
                SELECT p.id, p.sku, p.name, s.quantity, s.low_stock_threshold,
                       w.id, w.name
                FROM stock s
                JOIN products p ON s.product_id = p.id
                JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.quantity < s.low_stock_threshold
                  AND p.is_active = TRUE
                  AND s.warehouse_id = %s
                ORDER BY (s.low_stock_threshold - s.quantity) DESC
                """,
                (warehouse_id,),
            )
        else:
            cur.execute(
                """
                SELECT p.id, p.sku, p.name, s.quantity, s.low_stock_threshold,
                       w.id, w.name
                FROM stock s
                JOIN products p ON s.product_id = p.id
                JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.quantity < s.low_stock_threshold
                  AND p.is_active = TRUE
                ORDER BY (s.low_stock_threshold - s.quantity) DESC
                """
            )

        rows = cur.fetchall()
        return success({
            "low_stock_products": [
                {
                    "product_id": str(r[0]),
                    "sku": r[1],
                    "product_name": r[2],
                    "quantity": r[3],
                    "low_stock_threshold": r[4],
                    "deficit": r[4] - r[3],
                    "warehouse_id": str(r[5]),
                    "warehouse_name": r[6],
                }
                for r in rows
            ],
            "total": len(rows),
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
