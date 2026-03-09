"""Lambda: omnidesk-product-list
GET /api/products           — List products (paginated, filterable)
GET /api/products/{id}      — Get single product
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    product_id = path_params.get("id")

    if product_id:
        return _get_single(product_id)
    return _list(event)


def _get_single(product_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name as category_name,
                   p.unit_price, p.unit, p.is_active, p.created_by, p.created_at, p.updated_at
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.id = %s
            """,
            (product_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Product not found", 404)
        if not row[8]:
            return error("Product has been deactivated", 404)

        return success({
            "id": str(row[0]),
            "sku": row[1],
            "name": row[2],
            "description": row[3],
            "category_id": str(row[4]) if row[4] else None,
            "category_name": row[5],
            "unit_price": str(row[6]),
            "unit": row[7],
            "created_by": str(row[9]),
            "created_at": str(row[10]),
            "updated_at": str(row[11]),
        })
    finally:
        conn.close()


def _list(event):
    qs = event.get("queryStringParameters") or {}
    page = max(int(qs.get("page", 1)), 1)
    limit = min(max(int(qs.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    category_id = qs.get("category_id")
    search = qs.get("search", "").strip()

    conditions = ["p.is_active = TRUE"]
    params = []

    if category_id:
        conditions.append("p.category_id = %s")
        params.append(category_id)
    if search:
        conditions.append("(p.name ILIKE %s OR p.sku ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = " AND ".join(conditions)

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Count
        cur.execute(f"SELECT COUNT(*) FROM products p WHERE {where}", params)
        total = cur.fetchone()[0]

        # Fetch
        cur.execute(
            f"""
            SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name as category_name,
                   p.unit_price, p.unit, p.created_at
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE {where}
            ORDER BY p.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()

        return success({
            "products": [
                {
                    "id": str(r[0]),
                    "sku": r[1],
                    "name": r[2],
                    "description": r[3],
                    "category_id": str(r[4]) if r[4] else None,
                    "category_name": r[5],
                    "unit_price": str(r[6]),
                    "unit": r[7],
                    "created_at": str(r[8]),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "limit": limit,
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
