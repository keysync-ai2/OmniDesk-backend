"""Lambda: omnidesk-report-generate
POST /api/reports/generate
Generates markdown + Chart.js HTML reports, uploads to S3, returns presigned URL.
Supports: sales, stock, invoice_summary report types.
"""
import json
import os
import uuid
import string
import random
from datetime import datetime, timedelta
import boto3
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.audit import log_action
from utils.report_builder import build_report_html

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
s3 = boto3.client("s3", region_name="us-east-1")


def _generate_number():
    date_part = datetime.utcnow().strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"RPT-{date_part}-{rand_part}"


# --------------- Sales Report ---------------

def _build_sales_report(cur, from_date, to_date, filters):
    """Build markdown + charts for sales report."""
    category_id = filters.get("category_id")

    # Summary: total orders, total revenue
    cur.execute(
        """SELECT COUNT(*), COALESCE(SUM(total_amount), 0)
           FROM orders WHERE status != 'cancelled'
           AND created_at >= %s AND created_at < %s""",
        (from_date, to_date),
    )
    order_count, total_revenue = cur.fetchone()

    # Top 5 products by revenue
    cur.execute(
        """SELECT p.name, p.sku, SUM(oi.quantity) as qty, SUM(oi.total_price) as rev
           FROM order_items oi
           JOIN orders o ON oi.order_id = o.id
           JOIN products p ON oi.product_id = p.id
           WHERE o.status != 'cancelled' AND o.created_at >= %s AND o.created_at < %s
           GROUP BY p.id, p.name, p.sku
           ORDER BY rev DESC LIMIT 5""",
        (from_date, to_date),
    )
    top_products = cur.fetchall()

    # Daily breakdown
    cur.execute(
        """SELECT DATE(created_at) as day, COUNT(*), COALESCE(SUM(total_amount), 0)
           FROM orders WHERE status != 'cancelled'
           AND created_at >= %s AND created_at < %s
           GROUP BY DATE(created_at) ORDER BY day""",
        (from_date, to_date),
    )
    daily = cur.fetchall()

    # Order status breakdown
    cur.execute(
        """SELECT status, COUNT(*) FROM orders
           WHERE created_at >= %s AND created_at < %s
           GROUP BY status ORDER BY COUNT(*) DESC""",
        (from_date, to_date),
    )
    status_breakdown = cur.fetchall()

    # Build markdown
    md = f"""# Sales Summary

| Metric | Value |
|--------|-------|
| **Total Orders** | {order_count} |
| **Total Revenue** | Rs. {total_revenue:,.2f} |
| **Period** | {from_date} to {to_date} |

# Order Status

| Status | Count |
|--------|-------|
"""
    for status, count in status_breakdown:
        md += f"| {status.title()} | {count} |\n"

    md += "\n# Top 5 Products by Revenue\n\n"
    if top_products:
        md += "| # | Product | SKU | Qty Sold | Revenue |\n|---|---------|-----|----------|---------|\n"
        for i, (name, sku, qty, rev) in enumerate(top_products, 1):
            md += f"| {i} | {name} | {sku} | {qty} | Rs. {rev:,.2f} |\n"
    else:
        md += "> No sales data for this period.\n"

    md += "\n# Daily Breakdown\n\n"
    if daily:
        md += "| Date | Orders | Revenue |\n|------|--------|---------|\n"
        for day, cnt, rev in daily:
            md += f"| {day} | {cnt} | Rs. {rev:,.2f} |\n"
    else:
        md += "> No daily data available.\n"

    # Charts
    charts = []
    if daily:
        charts.append({
            "id": "daily-revenue-chart",
            "type": "line",
            "data": {
                "labels": [str(d[0]) for d in daily],
                "datasets": [{
                    "label": "Daily Revenue (Rs.)",
                    "data": [float(d[2]) for d in daily],
                    "borderColor": "#1a73e8",
                    "backgroundColor": "rgba(26, 115, 232, 0.1)",
                    "fill": True,
                    "tension": 0.3,
                }],
            },
            "options": {"responsive": True, "plugins": {"title": {"display": True, "text": "Daily Revenue Trend"}}},
        })

    if top_products:
        charts.append({
            "id": "top-products-chart",
            "type": "bar",
            "data": {
                "labels": [p[0][:20] for p in top_products],
                "datasets": [{
                    "label": "Revenue (Rs.)",
                    "data": [float(p[3]) for p in top_products],
                    "backgroundColor": ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9334e6"],
                }],
            },
            "options": {"responsive": True, "plugins": {"title": {"display": True, "text": "Top 5 Products by Revenue"}}},
        })

    return md, charts, f"Sales Report • {from_date} to {to_date}"


# --------------- Stock Report ---------------

def _build_stock_report(cur, from_date, to_date, filters):
    """Build markdown + charts for stock report."""
    warehouse_id = filters.get("warehouse_id")

    # Stock levels
    wh_filter = "AND s.warehouse_id = %s" if warehouse_id else ""
    wh_params = [warehouse_id] if warehouse_id else []

    cur.execute(
        f"""SELECT p.name, p.sku, s.quantity, s.low_stock_threshold, w.name as wh_name
            FROM stock s
            JOIN products p ON s.product_id = p.id
            LEFT JOIN warehouses w ON s.warehouse_id = w.id
            WHERE p.is_active = TRUE {wh_filter}
            ORDER BY s.quantity ASC LIMIT 50""",
        wh_params,
    )
    stock_rows = cur.fetchall()

    # Low stock count
    cur.execute(
        f"""SELECT COUNT(*) FROM stock s
            JOIN products p ON s.product_id = p.id
            WHERE p.is_active = TRUE AND s.quantity < s.low_stock_threshold {wh_filter}""",
        wh_params,
    )
    low_stock_count = cur.fetchone()[0]

    # Total products in stock
    cur.execute(
        f"SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM stock s WHERE 1=1 {wh_filter}",
        wh_params,
    )
    total_entries, total_qty = cur.fetchone()

    # Build markdown
    md = f"""# Stock Summary

| Metric | Value |
|--------|-------|
| **Total Stock Entries** | {total_entries} |
| **Total Units** | {total_qty:,} |
| **Low Stock Alerts** | {low_stock_count} |

# Stock Levels

| Product | SKU | Quantity | Threshold | Warehouse | Status |
|---------|-----|----------|-----------|-----------|--------|
"""
    healthy = 0
    low = 0
    critical = 0
    for name, sku, qty, threshold, wh in stock_rows:
        if qty < threshold:
            if qty == 0:
                status = "🔴 Critical"
                critical += 1
            else:
                status = "🟡 Low"
                low += 1
        else:
            status = "🟢 OK"
            healthy += 1
        md += f"| {name} | {sku} | {qty} | {threshold} | {wh or '-'} | {status} |\n"

    # Charts
    charts = []
    if stock_rows:
        charts.append({
            "id": "stock-status-chart",
            "type": "doughnut",
            "data": {
                "labels": ["Healthy", "Low", "Critical"],
                "datasets": [{
                    "data": [healthy, low, critical],
                    "backgroundColor": ["#34a853", "#fbbc04", "#ea4335"],
                }],
            },
            "options": {"responsive": True, "plugins": {"title": {"display": True, "text": "Stock Health Distribution"}}},
        })

        # Top 10 by quantity
        top_stock = sorted(stock_rows, key=lambda x: x[2], reverse=True)[:10]
        charts.append({
            "id": "top-stock-chart",
            "type": "bar",
            "data": {
                "labels": [r[0][:20] for r in top_stock],
                "datasets": [{
                    "label": "Quantity",
                    "data": [r[2] for r in top_stock],
                    "backgroundColor": "#1a73e8",
                }],
            },
            "options": {"responsive": True, "plugins": {"title": {"display": True, "text": "Top 10 Products by Stock Quantity"}}},
        })

    return md, charts, "Stock Report"


# --------------- Invoice Summary Report ---------------

def _build_invoice_report(cur, from_date, to_date, filters):
    """Build markdown + charts for invoice summary report."""
    cur.execute(
        """SELECT payment_status, COUNT(*), COALESCE(SUM(total_amount), 0)
           FROM invoices WHERE 1=1
           AND created_at >= %s AND created_at < %s
           GROUP BY payment_status""",
        (from_date, to_date),
    )
    status_rows = cur.fetchall()

    total_invoices = sum(r[1] for r in status_rows)
    total_amount = sum(r[2] for r in status_rows)

    # Overdue invoices
    cur.execute(
        """SELECT i.invoice_number, o.customer_name, i.total_amount, i.due_date
           FROM invoices i
           LEFT JOIN orders o ON i.order_id = o.id
           WHERE i.payment_status = 'unpaid' AND i.due_date < NOW()
           AND i.created_at >= %s AND i.created_at < %s
           ORDER BY i.due_date ASC LIMIT 20""",
        (from_date, to_date),
    )
    overdue = cur.fetchall()

    md = f"""# Invoice Summary

| Metric | Value |
|--------|-------|
| **Total Invoices** | {total_invoices} |
| **Total Amount** | Rs. {total_amount:,.2f} |
| **Period** | {from_date} to {to_date} |

# Payment Status Breakdown

| Status | Count | Amount |
|--------|-------|--------|
"""
    for status, count, amount in status_rows:
        md += f"| {status.title()} | {count} | Rs. {amount:,.2f} |\n"

    md += f"\n# Overdue Invoices ({len(overdue)})\n\n"
    if overdue:
        md += "| Invoice # | Customer | Amount | Due Date |\n|-----------|----------|--------|----------|\n"
        for inv_num, customer, amount, due in overdue:
            md += f"| {inv_num} | {customer or '-'} | Rs. {amount:,.2f} | {due} |\n"
    else:
        md += "> No overdue invoices.\n"

    # Charts
    charts = []
    if status_rows:
        charts.append({
            "id": "payment-status-chart",
            "type": "doughnut",
            "data": {
                "labels": [r[0].title() for r in status_rows],
                "datasets": [{
                    "data": [float(r[2]) for r in status_rows],
                    "backgroundColor": ["#34a853", "#ea4335", "#fbbc04", "#9334e6"],
                }],
            },
            "options": {"responsive": True, "plugins": {"title": {"display": True, "text": "Invoice Amount by Payment Status"}}},
        })

    return md, charts, f"Invoice Summary • {from_date} to {to_date}"


REPORT_BUILDERS = {
    "sales": _build_sales_report,
    "stock": _build_stock_report,
    "invoice_summary": _build_invoice_report,
}


def _handler(event, context):
    body = json.loads(event.get("body") or "{}")
    user = event["user"]

    report_type = (body.get("report_type") or "").strip().lower()
    if report_type not in REPORT_BUILDERS:
        return error(f"Invalid report_type. Must be one of: {', '.join(REPORT_BUILDERS.keys())}", 400)

    # Date range defaults to last 30 days
    to_date = body.get("to_date") or datetime.utcnow().strftime("%Y-%m-%d")
    from_date = body.get("from_date") or (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    filters = body.get("filters") or {}

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Build report content
        builder = REPORT_BUILDERS[report_type]
        markdown, charts, subtitle = builder(cur, from_date, to_date, filters)

        title = f"{report_type.replace('_', ' ').title()} Report"
        html = build_report_html(title, markdown, charts, subtitle)

        # Upload to S3
        report_number = _generate_number()
        s3_key = f"reports/{report_number}.html"
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
        )

        # Generate presigned URL (15 min)
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": s3_key},
            ExpiresIn=900,
        )

        # Save to DB
        cur.execute(
            """INSERT INTO reports (title, report_type, s3_key, source_module, filters, generated_by)
               VALUES (%s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (f"{title} — {from_date} to {to_date}", report_type, s3_key,
             report_type, json.dumps({"from_date": from_date, "to_date": to_date, **filters}),
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
            "from_date": from_date,
            "to_date": to_date,
            "s3_key": s3_key,
            "url": url,
            "created_at": str(row[1]),
        }, 201)
    except Exception as e:
        conn.rollback()
        return error(f"Failed to generate report: {str(e)}", 500)
    finally:
        conn.close()


handler = require_auth(_handler, min_role="manager")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
