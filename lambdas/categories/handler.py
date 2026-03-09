"""Lambda: omnidesk-categories
POST /api/categories        — Create category
GET  /api/categories        — List categories
GET  /api/categories/{id}   — Get single category
"""
import json
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action


def _create(event, context):
    body = json.loads(event.get("body") or "{}")
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None

    if not name:
        return error("Category name is required", 400)

    user = event["user"]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM categories WHERE name = %s AND is_active = TRUE", (name,))
        if cur.fetchone():
            return error("Category already exists", 409)

        cur.execute(
            "INSERT INTO categories (name, description) VALUES (%s, %s) RETURNING id, name, description, created_at",
            (name, description),
        )
        row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "create_category", "categories", entity_id=str(row[0]),
                   details={"name": row[1]})

        return success({
            "id": str(row[0]), "name": row[1], "description": row[2], "created_at": str(row[3]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to create category: {str(e)}", 500)
    finally:
        conn.close()


def _list(event, context):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, name, description, created_at FROM categories WHERE is_active = TRUE ORDER BY name")
        rows = cur.fetchall()
        return success({
            "categories": [
                {"id": str(r[0]), "name": r[1], "description": r[2], "created_at": str(r[3])}
                for r in rows
            ],
            "total": len(rows),
        })
    finally:
        conn.close()


def _get(event, context, category_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, is_active, created_at FROM categories WHERE id = %s",
            (category_id,),
        )
        row = cur.fetchone()
        if not row or not row[3]:
            return error("Category not found", 404)
        return success({
            "id": str(row[0]), "name": row[1], "description": row[2], "created_at": str(row[4]),
        })
    finally:
        conn.close()


create_handler = require_auth(_create, min_role="manager")


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)

    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}

    if method == "POST":
        return create_handler(event, context)

    if method == "GET":
        cat_id = path_params.get("id")
        if cat_id:
            return _get(event, context, cat_id)
        return _list(event, context)

    return error("Method not allowed", 405)
