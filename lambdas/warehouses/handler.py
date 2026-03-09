"""Lambda: omnidesk-warehouses
POST /api/warehouses       — Create warehouse
GET  /api/warehouses       — List warehouses
GET  /api/warehouses/{id}  — Get single warehouse
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action


def _create(event, context):
    body = json.loads(event.get("body") or "{}")
    name = (body.get("name") or "").strip()
    address = (body.get("address") or "").strip() or None

    if not name:
        return error("Warehouse name is required", 400)

    user = event["user"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO warehouses (name, address) VALUES (%s, %s) RETURNING id, name, address, created_at",
            (name, address),
        )
        row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "create_warehouse", "warehouses", entity_id=str(row[0]),
                   details={"name": row[1]})

        return success({
            "id": str(row[0]), "name": row[1], "address": row[2], "created_at": str(row[3]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to create warehouse: {str(e)}", 500)
    finally:
        conn.close()


def _list(event, context):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, address, created_at FROM warehouses WHERE is_active = TRUE ORDER BY name")
        rows = cur.fetchall()
        return success({
            "warehouses": [
                {"id": str(r[0]), "name": r[1], "address": r[2], "created_at": str(r[3])}
                for r in rows
            ],
            "total": len(rows),
        })
    finally:
        conn.close()


def _get(event, context, warehouse_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, address, is_active, created_at FROM warehouses WHERE id = %s",
            (warehouse_id,),
        )
        row = cur.fetchone()
        if not row or not row[3]:
            return error("Warehouse not found", 404)
        return success({
            "id": str(row[0]), "name": row[1], "address": row[2], "created_at": str(row[4]),
        })
    finally:
        conn.close()


create_handler = require_auth(_create, min_role="admin")


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)

    path_params = event.get("pathParameters") or {}

    if method == "POST":
        return create_handler(event, context)
    if method == "GET":
        wh_id = path_params.get("id")
        if wh_id:
            return _get(event, context, wh_id)
        return _list(event, context)

    return error("Method not allowed", 405)
