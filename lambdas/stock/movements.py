"""Lambda: omnidesk-stock-movements
GET /api/stock/movements/{product_id}  — Movement history for a product
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
    page = max(int(qs.get("page", 1)), 1)
    limit = min(max(int(qs.get("limit", 50)), 1), 100)
    offset = (page - 1) * limit

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Verify product exists
        cur.execute("SELECT id, name, sku FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()
        if not product:
            return error("Product not found", 404)

        conditions = ["sm.product_id = %s"]
        params = [product_id]

        if warehouse_id:
            conditions.append("sm.warehouse_id = %s")
            params.append(warehouse_id)

        where = " AND ".join(conditions)

        # Count
        cur.execute(f"SELECT COUNT(*) FROM stock_movements sm WHERE {where}", params)
        total = cur.fetchone()[0]

        # Fetch
        cur.execute(
            f"""
            SELECT sm.id, sm.movement_type, sm.quantity, sm.reason,
                   sm.performed_by, u.full_name, sm.created_at,
                   w.id, w.name
            FROM stock_movements sm
            LEFT JOIN users u ON sm.performed_by = u.id
            LEFT JOIN warehouses w ON sm.warehouse_id = w.id
            WHERE {where}
            ORDER BY sm.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()

        return success({
            "product_id": str(product[0]),
            "product_name": product[1],
            "sku": product[2],
            "movements": [
                {
                    "id": str(r[0]),
                    "movement_type": r[1],
                    "quantity": r[2],
                    "reason": r[3],
                    "performed_by": str(r[4]) if r[4] else None,
                    "performed_by_name": r[5],
                    "created_at": str(r[6]),
                    "warehouse_id": str(r[7]) if r[7] else None,
                    "warehouse_name": r[8],
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "limit": limit,
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
