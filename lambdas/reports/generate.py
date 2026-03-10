"""Lambda: omnidesk-report-generate
POST /api/reports/generate
Generates component-based HTML reports with interactive tables + Chart.js charts.
Uploads to S3, returns CloudFront signed URL.
Preset types: sales, stock, invoice_summary. Also supports custom component lists.
"""
import json
import os
import string
import random
from datetime import datetime, timedelta
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.report_builder import build_report_html
from utils.report_templates import REPORT_BUILDERS
from utils.cloudfront_signer import generate_signed_url

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
s3 = boto3.client("s3", region_name="us-east-1")


def _generate_number():
    date_part = datetime.utcnow().strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"RPT-{date_part}-{rand_part}"


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    report_type = (body.get("report_type") or "").strip().lower()

    # Support custom component lists (for MCP/AI-built reports)
    custom_components = body.get("components")
    custom_title = body.get("title")

    if custom_components and custom_title:
        components = custom_components
        subtitle = body.get("subtitle", "Custom Report")
        title = custom_title
        report_type = report_type or "custom"
    elif report_type in REPORT_BUILDERS:
        to_date = body.get("to_date") or datetime.utcnow().strftime("%Y-%m-%d")
        from_date = body.get("from_date") or (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        filters = body.get("filters") or {}

        conn = get_connection()
        try:
            cur = conn.cursor()
            builder = REPORT_BUILDERS[report_type]
            components, subtitle = builder(cur, from_date, to_date, filters)
            title = f"{report_type.replace('_', ' ').title()} Report"
        finally:
            conn.close()
    else:
        return error(f"Provide a valid report_type ({', '.join(REPORT_BUILDERS.keys())}) or custom components with title", 400)

    # Build HTML
    html = build_report_html(title, components, subtitle)

    # Upload to S3
    report_number = _generate_number()
    s3_key = f"reports/{report_number}.html"
    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=html.encode("utf-8"), ContentType="text/html")
    url = generate_signed_url(s3_key, expires_in=300)

    # Save to DB
    to_date_val = body.get("to_date") or datetime.utcnow().strftime("%Y-%m-%d")
    from_date_val = body.get("from_date") or (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO reports (title, report_type, s3_key, source_module, filters, generated_by)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (f"{title} — {from_date_val} to {to_date_val}", report_type, s3_key,
             report_type, json.dumps({"from_date": from_date_val, "to_date": to_date_val}),
             user["user_id"]),
        )
        row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "generate_report", "reports",
                   entity_id=str(row[0]), details={"report_type": report_type, "s3_key": s3_key})

        return success({
            "id": str(row[0]),
            "report_number": report_number,
            "title": title,
            "report_type": report_type,
            "from_date": from_date_val,
            "to_date": to_date_val,
            "s3_key": s3_key,
            "url": url,
            "created_at": str(row[1]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to save report: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
