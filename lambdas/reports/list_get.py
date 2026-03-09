"""Lambda: omnidesk-report-list
GET /api/reports        — List reports (paginated, filterable by type)
GET /api/reports/{id}   — Get single report with fresh presigned URL
"""
import json
import os
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
s3 = boto3.client("s3", region_name="us-east-1")


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    report_id = path_params.get("id")

    if report_id:
        return _get_single(report_id)
    return _list(event)


def _get_single(report_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, title, report_type, s3_key, source_module, filters,
                      generated_by, is_active, created_at
               FROM reports WHERE id = %s""",
            (report_id,),
        )
        r = cur.fetchone()
        if not r:
            return error("Report not found", 404)
        if not r[7]:
            return error("Report has been deleted", 404)

        # Fresh presigned URL
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": r[3]},
            ExpiresIn=900,
        )

        return success({
            "id": str(r[0]),
            "title": r[1],
            "report_type": r[2],
            "s3_key": r[3],
            "source_module": r[4],
            "filters": r[5] if isinstance(r[5], dict) else json.loads(r[5] or '{}'),
            "generated_by": str(r[6]) if r[6] else None,
            "url": url,
            "created_at": str(r[8]),
        })
    finally:
        conn.close()


def _list(event):
    qs = event.get("queryStringParameters") or {}
    page = max(int(qs.get("page", 1)), 1)
    limit = min(max(int(qs.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    report_type = qs.get("report_type")

    conditions = ["is_active = TRUE"]
    params = []
    if report_type:
        conditions.append("report_type = %s")
        params.append(report_type)

    where = " AND ".join(conditions)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM reports WHERE {where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"""SELECT id, title, report_type, s3_key, created_at
                FROM reports WHERE {where}
                ORDER BY created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()

        return success({
            "reports": [{
                "id": str(r[0]),
                "title": r[1],
                "report_type": r[2],
                "s3_key": r[3],
                "created_at": str(r[4]),
            } for r in rows],
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
