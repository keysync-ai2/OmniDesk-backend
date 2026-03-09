"""Lambda: omnidesk-form-create
POST /api/forms        — Create a form definition + generate hosted HTML
GET  /api/forms        — List all forms
GET  /api/forms/{id}   — Get single form details
"""
import json
import os
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.form_builder import build_form_html

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
API_BASE = "https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev"
s3 = boto3.client("s3", region_name="us-east-1")


def _create(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip() or None
    fields = body.get("fields") or []
    theme = (body.get("theme") or "default").strip().lower()

    if not name:
        return error("Form name is required", 400)
    if not fields:
        return error("At least one field is required", 400)

    # Validate field definitions
    valid_types = {"text", "email", "phone", "number", "date", "url", "select", "radio", "checkbox", "textarea", "file"}
    for f in fields:
        if not f.get("name"):
            return error("Each field must have a 'name'", 400)
        if f.get("type") and f["type"] not in valid_types:
            return error(f"Invalid field type '{f['type']}'. Valid: {', '.join(sorted(valid_types))}", 400)

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(
            """INSERT INTO forms (name, description, schema_json, theme, created_by)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (name, description, json.dumps(fields), theme, user["user_id"]),
        )
        row = cur.fetchone()
        form_id = str(row[0])

        # Generate and host HTML form on S3
        submit_url = f"{API_BASE}/api/forms/{form_id}/submit"
        html = build_form_html(name, description, fields, submit_url, theme)

        s3_key = f"forms/{form_id}/form.html"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
        )

        # Get public presigned URL (long-lived: 7 days)
        s3_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=604800,
        )

        # Update form with S3 URL
        cur.execute("UPDATE forms SET s3_url = %s WHERE id = %s", (s3_key, form_id))
        conn.commit()

        log_action(user["user_id"], "create_form", "forms",
                   entity_id=form_id, details={"name": name, "fields": len(fields)})

        return success({
            "id": form_id,
            "name": name,
            "description": description,
            "fields": fields,
            "theme": theme,
            "form_url": s3_url,
            "created_at": str(row[1]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to create form: {str(e)}", 500)
    finally:
        conn.close()


def _list(event, context):
    qs = event.get("queryStringParameters") or {}
    page = max(int(qs.get("page", 1)), 1)
    limit = min(max(int(qs.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM forms WHERE is_active = TRUE")
        total = cur.fetchone()[0]

        cur.execute(
            """SELECT id, name, description, theme, s3_url, created_at
               FROM forms WHERE is_active = TRUE
               ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            [limit, offset],
        )
        rows = cur.fetchall()

        forms = []
        for r in rows:
            form_url = None
            if r[4]:
                form_url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": S3_BUCKET, "Key": r[4]},
                    ExpiresIn=604800,
                )
            forms.append({
                "id": str(r[0]),
                "name": r[1],
                "description": r[2],
                "theme": r[3],
                "form_url": form_url,
                "created_at": str(r[5]),
            })

        return success({"forms": forms, "total": total, "page": page, "limit": limit})
    finally:
        conn.close()


def _get_single(form_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, description, schema_json, theme, s3_url, is_active, created_by, created_at
               FROM forms WHERE id = %s""",
            (form_id,),
        )
        r = cur.fetchone()
        if not r:
            return error("Form not found", 404)
        if not r[6]:
            return error("Form has been deactivated", 404)

        form_url = None
        if r[5]:
            form_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET, "Key": r[5]},
                ExpiresIn=604800,
            )

        schema = r[3] if isinstance(r[3], list) else json.loads(r[3] or '[]')

        return success({
            "id": str(r[0]),
            "name": r[1],
            "description": r[2],
            "fields": schema,
            "theme": r[4],
            "form_url": form_url,
            "created_by": str(r[7]) if r[7] else None,
            "created_at": str(r[8]),
        })
    finally:
        conn.close()


create_handler = require_auth(_create, min_role="manager")
list_handler = require_auth(_list, min_role="viewer")
get_handler = require_auth(_get_single, min_role="viewer")


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)

    path_params = event.get("pathParameters") or {}
    form_id = path_params.get("id")

    if method == "POST":
        return create_handler(event, context)
    if method == "GET" and form_id:
        # Inject form_id for the auth wrapper
        event["_form_id"] = form_id
        return _get_auth_wrapper(event, context)
    if method == "GET":
        return list_handler(event, context)

    return error("Method not allowed", 405)


def _get_auth_inner(event, context):
    return _get_single(event["_form_id"])

_get_auth_wrapper = require_auth(_get_auth_inner, min_role="viewer")
