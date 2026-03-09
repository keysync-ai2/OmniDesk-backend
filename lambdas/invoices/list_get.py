"""Lambda: omnidesk-invoice-list
GET /api/invoices         — list invoices (paginated, filterable)
GET /api/invoices/{id}    — get single invoice with order details
"""
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    invoice_id = path_params.get("id")

    # Route to download/send if path contains those segments
    path = event.get("path") or ""
    if "/download" in path or "/send" in path:
        return error("Wrong endpoint. Use the dedicated download/send Lambda.", 400)

    if invoice_id:
        return _get_single(invoice_id)
    return _list_invoices(event)


def _get_single(invoice_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT i.id, i.invoice_number, i.order_id, o.order_number, o.customer_name,
                      o.customer_email, i.subtotal, i.tax_rate, i.tax_amount, i.total_amount,
                      i.payment_status, i.status, i.due_date, i.sent_at, i.created_by,
                      i.created_at, i.pdf_s3_key
               FROM invoices i JOIN orders o ON i.order_id = o.id
               WHERE i.id = %s""",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return error("Invoice not found", 404)

        return success({
            "id": str(row[0]), "invoice_number": row[1],
            "order_id": str(row[2]), "order_number": row[3],
            "customer_name": row[4], "customer_email": row[5],
            "subtotal": str(row[6]), "tax_rate": str(row[7]),
            "tax_amount": str(row[8]), "total_amount": str(row[9]),
            "payment_status": row[10], "status": row[11],
            "due_date": str(row[12]) if row[12] else None,
            "sent_at": str(row[13]) if row[13] else None,
            "created_by": str(row[14]), "created_at": str(row[15]),
            "has_file": bool(row[16]),
        })
    finally:
        conn.close()


def _list_invoices(event):
    qsp = event.get("queryStringParameters") or {}
    page = max(int(qsp.get("page", 1)), 1)
    limit = min(max(int(qsp.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    payment_status = (qsp.get("payment_status") or "").strip().lower() or None
    from_date = qsp.get("from_date")
    to_date = qsp.get("to_date")
    search = (qsp.get("search") or "").strip()

    conditions = []
    params = []

    if payment_status:
        conditions.append("i.payment_status = %s")
        params.append(payment_status)
    if from_date:
        conditions.append("i.created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("i.created_at <= %s")
        params.append(to_date)
    if search:
        conditions.append("(i.invoice_number ILIKE %s OR o.customer_name ILIKE %s OR o.order_number ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM invoices i JOIN orders o ON i.order_id = o.id {where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"""SELECT i.id, i.invoice_number, o.order_number, o.customer_name,
                       i.total_amount, i.payment_status, i.status, i.due_date, i.created_at
                FROM invoices i JOIN orders o ON i.order_id = o.id
                {where}
                ORDER BY i.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()

        return success({
            "invoices": [
                {
                    "id": str(r[0]), "invoice_number": r[1], "order_number": r[2],
                    "customer_name": r[3], "total_amount": str(r[4]),
                    "payment_status": r[5], "status": r[6],
                    "due_date": str(r[7]) if r[7] else None,
                    "created_at": str(r[8]),
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if total > 0 else 1,
        })
    finally:
        conn.close()


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
