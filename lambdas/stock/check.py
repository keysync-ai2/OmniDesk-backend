"""Lambda: omnidesk-stock-check
GET /api/stock/{product_id}            — Get stock level for a product
GET /api/stock/{product_id}?warehouse_id=xxx — Per-warehouse stock
"""
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("product_id")
    if not product_id:
        return error("Product ID is required", 400)

    qs = event.get("queryStringParameters") or {}
    warehouse_id = qs.get("warehouse_id")

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Verify product exists
        cur.execute("SELECT id, name, sku FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
        product = cur.fetchone()
        if not product:
            return error("Product not found", 404)

        if warehouse_id:
            cur.execute(
                """
                SELECT s.quantity, s.low_stock_threshold, w.name as warehouse_name, w.id
                FROM stock s
                JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.product_id = %s AND s.warehouse_id = %s
                """,
                (product_id, warehouse_id),
            )
            row = cur.fetchone()
            if not row:
                return success({
                    "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                    "quantity": 0, "low_stock_threshold": 10,
                    "warehouse_id": warehouse_id, "warehouse_name": None,
                    "is_low_stock": True,
                })
            return success({
                "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                "quantity": row[0], "low_stock_threshold": row[1],
                "warehouse_id": str(row[3]), "warehouse_name": row[2],
                "is_low_stock": row[0] < row[1],
            })
        else:
            # Aggregate across all warehouses
            cur.execute(
                """
                SELECT COALESCE(SUM(s.quantity), 0) as total_qty, MIN(s.low_stock_threshold) as threshold
                FROM stock s
                WHERE s.product_id = %s
                """,
                (product_id,),
            )
            agg = cur.fetchone()
            total_qty = agg[0]
            threshold = agg[1] or 10

            # Also get per-warehouse breakdown
            cur.execute(
                """
                SELECT w.id, w.name, s.quantity, s.low_stock_threshold
                FROM stock s
                JOIN warehouses w ON s.warehouse_id = w.id
                WHERE s.product_id = %s
                ORDER BY w.name
                """,
                (product_id,),
            )
            warehouses = [
                {"warehouse_id": str(r[0]), "warehouse_name": r[1], "quantity": r[2], "low_stock_threshold": r[3]}
                for r in cur.fetchall()
            ]

            return success({
                "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                "total_quantity": total_qty, "low_stock_threshold": threshold,
                "is_low_stock": total_qty < threshold,
                "warehouses": warehouses,
            })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
