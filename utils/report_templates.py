"""Report templates — reusable report builders that produce component lists.

Each builder queries the DB and returns (components, subtitle).
Components are structured dicts consumed by report_builder.build_report_html().

Used by both the standalone report Lambda and the MCP server.
"""


def _fmt_amount(val):
    """Format a number as Rs. X,XXX.XX"""
    return f"Rs. {val:,.2f}" if val else "Rs. 0.00"


# ── Sales Report ─────────────────────────────────────────────────────

def build_sales_report(cur, from_date, to_date, filters):
    """Build component list for sales report."""
    components = []

    # Summary metrics
    cur.execute(
        """SELECT COUNT(*), COALESCE(SUM(total_amount), 0)
           FROM orders WHERE status != 'cancelled'
           AND created_at >= %s AND created_at < %s""",
        (from_date, to_date),
    )
    order_count, total_revenue = cur.fetchone()

    cur.execute(
        """SELECT COALESCE(AVG(total_amount), 0) FROM orders
           WHERE status != 'cancelled' AND created_at >= %s AND created_at < %s""",
        (from_date, to_date),
    )
    avg_order = cur.fetchone()[0]

    cur.execute(
        """SELECT status, COUNT(*) FROM orders
           WHERE created_at >= %s AND created_at < %s
           GROUP BY status ORDER BY COUNT(*) DESC""",
        (from_date, to_date),
    )
    status_breakdown = cur.fetchall()
    delivered = sum(c for s, c in status_breakdown if s == "delivered")

    components.append({
        "type": "summary_cards",
        "cards": [
            {"label": "Total Orders", "value": str(order_count), "icon": "📦"},
            {"label": "Total Revenue", "value": _fmt_amount(total_revenue), "icon": "💰"},
            {"label": "Avg. Order Value", "value": _fmt_amount(avg_order), "icon": "📊"},
            {"label": "Delivered", "value": str(delivered), "icon": "✅"},
        ],
    })

    # Charts: daily revenue + order status
    cur.execute(
        """SELECT DATE(created_at) as day, COUNT(*), COALESCE(SUM(total_amount), 0)
           FROM orders WHERE status != 'cancelled'
           AND created_at >= %s AND created_at < %s
           GROUP BY DATE(created_at) ORDER BY day""",
        (from_date, to_date),
    )
    daily = cur.fetchall()

    chart_children = []
    if daily:
        chart_children.append({
            "type": "chart", "id": "daily-revenue", "title": "Daily Revenue Trend",
            "chart_type": "line",
            "data": {
                "labels": [str(d[0]) for d in daily],
                "datasets": [{
                    "label": "Revenue (Rs.)",
                    "data": [float(d[2]) for d in daily],
                    "borderColor": "#1a73e8",
                    "backgroundColor": "rgba(26,115,232,0.08)",
                    "fill": True, "tension": 0.3, "pointRadius": 3,
                }],
            },
        })

    if status_breakdown:
        chart_children.append({
            "type": "chart", "id": "order-status", "title": "Order Status Distribution",
            "chart_type": "doughnut",
            "data": {
                "labels": [s.title() for s, _ in status_breakdown],
                "datasets": [{
                    "data": [c for _, c in status_breakdown],
                    "backgroundColor": ["#1a73e8", "#34a853", "#fbbc04", "#ea4335", "#9334e6"],
                }],
            },
        })

    if chart_children:
        components.append({"type": "grid", "children": chart_children})

    # Product revenue table
    cur.execute(
        """SELECT p.name, p.sku, c.name as category,
                  SUM(oi.quantity) as qty_sold, SUM(oi.total_price) as revenue
           FROM order_items oi
           JOIN orders o ON oi.order_id = o.id
           JOIN products p ON oi.product_id = p.id
           LEFT JOIN categories c ON p.category_id = c.id
           WHERE o.status != 'cancelled' AND o.created_at >= %s AND o.created_at < %s
           GROUP BY p.id, p.name, p.sku, c.name
           ORDER BY revenue DESC""",
        (from_date, to_date),
    )
    product_rows = cur.fetchall()

    if product_rows:
        components.append({
            "type": "table", "id": "product-revenue", "title": "Product Revenue Breakdown",
            "columns": [
                {"name": "Product"}, {"name": "SKU"}, {"name": "Category"},
                {"name": "Qty Sold"}, {"name": "Revenue"},
            ],
            "rows": [
                [name, sku, cat or "-", int(qty), _fmt_amount(rev)]
                for name, sku, cat, qty, rev in product_rows
            ],
            "filterable_columns": [2],
            "page_size": 15,
        })

    # Daily breakdown table
    if daily:
        components.append({
            "type": "table", "id": "daily-breakdown", "title": "Daily Order Breakdown",
            "columns": [{"name": "Date"}, {"name": "Orders"}, {"name": "Revenue"}],
            "rows": [[str(day), int(cnt), _fmt_amount(rev)] for day, cnt, rev in daily],
            "page_size": 15,
        })

    # Status table
    if status_breakdown:
        components.append({
            "type": "table", "id": "status-breakdown", "title": "Order Status Summary",
            "columns": [
                {"name": "Status", "badges": {
                    "Pending": "yellow", "Confirmed": "blue", "Shipped": "blue",
                    "Delivered": "green", "Cancelled": "red",
                }},
                {"name": "Count"},
            ],
            "rows": [[s.title(), c] for s, c in status_breakdown],
            "page_size": 10,
        })

    return components, f"Sales Report • {from_date} to {to_date}"


# ── Stock Report ─────────────────────────────────────────────────────

def build_stock_report(cur, from_date, to_date, filters):
    """Build component list for stock report."""
    components = []
    warehouse_id = filters.get("warehouse_id")
    wh_filter = "AND s.warehouse_id = %s" if warehouse_id else ""
    wh_params = [warehouse_id] if warehouse_id else []

    cur.execute(
        f"SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM stock s WHERE 1=1 {wh_filter}",
        wh_params,
    )
    total_entries, total_qty = cur.fetchone()

    cur.execute(
        f"""SELECT COUNT(*) FROM stock s JOIN products p ON s.product_id = p.id
            WHERE p.is_active = TRUE AND s.quantity < s.low_stock_threshold {wh_filter}""",
        wh_params,
    )
    low_stock_count = cur.fetchone()[0]

    cur.execute(
        f"""SELECT COUNT(*) FROM stock s JOIN products p ON s.product_id = p.id
            WHERE p.is_active = TRUE AND s.quantity = 0 {wh_filter}""",
        wh_params,
    )
    out_of_stock = cur.fetchone()[0]

    components.append({
        "type": "summary_cards",
        "cards": [
            {"label": "Stock Entries", "value": str(total_entries), "icon": "📋"},
            {"label": "Total Units", "value": f"{total_qty:,}", "icon": "📦"},
            {"label": "Low Stock Alerts", "value": str(low_stock_count), "icon": "⚠️"},
            {"label": "Out of Stock", "value": str(out_of_stock), "icon": "🚫"},
        ],
    })

    # Full stock data
    cur.execute(
        f"""SELECT p.name, p.sku, c.name as category, s.quantity, s.low_stock_threshold,
                   w.name as warehouse
            FROM stock s
            JOIN products p ON s.product_id = p.id
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN warehouses w ON s.warehouse_id = w.id
            WHERE p.is_active = TRUE {wh_filter}
            ORDER BY s.quantity ASC""",
        wh_params,
    )
    stock_rows = cur.fetchall()

    healthy, low, critical = 0, 0, 0
    table_rows = []
    for name, sku, cat, qty, threshold, wh in stock_rows:
        if qty == 0:
            status = "Critical"
            critical += 1
        elif qty < threshold:
            status = "Low"
            low += 1
        else:
            status = "OK"
            healthy += 1
        table_rows.append([name, sku, cat or "-", int(qty), int(threshold), wh or "-", status])

    # Charts
    chart_children = [
        {
            "type": "chart", "id": "stock-health", "title": "Stock Health Distribution",
            "chart_type": "doughnut",
            "data": {
                "labels": ["Healthy", "Low", "Critical"],
                "datasets": [{"data": [healthy, low, critical],
                              "backgroundColor": ["#34a853", "#fbbc04", "#ea4335"]}],
            },
        },
    ]

    top_stock = sorted(stock_rows, key=lambda x: x[3], reverse=True)[:10]
    if top_stock:
        chart_children.append({
            "type": "chart", "id": "top-stock", "title": "Top 10 Products by Stock",
            "chart_type": "bar",
            "data": {
                "labels": [r[0][:25] for r in top_stock],
                "datasets": [{"label": "Quantity", "data": [r[3] for r in top_stock],
                              "backgroundColor": "#1a73e8"}],
            },
        })

    components.append({"type": "grid", "children": chart_children})

    if table_rows:
        components.append({
            "type": "table", "id": "stock-levels", "title": "Stock Levels",
            "columns": [
                {"name": "Product"}, {"name": "SKU"}, {"name": "Category"},
                {"name": "Quantity"}, {"name": "Threshold"}, {"name": "Warehouse"},
                {"name": "Status", "badges": {"OK": "green", "Low": "yellow", "Critical": "red"}},
            ],
            "rows": table_rows,
            "filterable_columns": [2, 5, 6],
            "page_size": 20,
        })

    return components, "Stock Report"


# ── Invoice Summary Report ───────────────────────────────────────────

def build_invoice_report(cur, from_date, to_date, filters):
    """Build component list for invoice summary report."""
    components = []

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
    paid_amount = sum(r[2] for r in status_rows if r[0] == "paid")

    components.append({
        "type": "summary_cards",
        "cards": [
            {"label": "Total Invoices", "value": str(total_invoices), "icon": "📄"},
            {"label": "Total Amount", "value": _fmt_amount(total_amount), "icon": "💰"},
            {"label": "Collected", "value": _fmt_amount(paid_amount), "icon": "✅"},
            {"label": "Outstanding", "value": _fmt_amount(total_amount - paid_amount), "icon": "⏳"},
        ],
    })

    if status_rows:
        components.append({
            "type": "chart", "id": "payment-status", "title": "Invoice Amount by Payment Status",
            "chart_type": "doughnut",
            "data": {
                "labels": [r[0].title() for r in status_rows],
                "datasets": [{
                    "data": [float(r[2]) for r in status_rows],
                    "backgroundColor": ["#34a853", "#ea4335", "#fbbc04", "#9334e6"],
                }],
            },
        })

        components.append({
            "type": "table", "id": "payment-breakdown", "title": "Payment Status Breakdown",
            "columns": [
                {"name": "Status", "badges": {
                    "Paid": "green", "Unpaid": "red", "Partial": "yellow", "Overdue": "red",
                }},
                {"name": "Count"}, {"name": "Amount"},
            ],
            "rows": [[s.title(), c, _fmt_amount(a)] for s, c, a in status_rows],
            "page_size": 10,
        })

    # All invoices
    cur.execute(
        """SELECT i.invoice_number, o.customer_name, i.total_amount,
                  i.payment_status, i.due_date, i.created_at
           FROM invoices i
           LEFT JOIN orders o ON i.order_id = o.id
           WHERE i.created_at >= %s AND i.created_at < %s
           ORDER BY i.created_at DESC""",
        (from_date, to_date),
    )
    all_invoices = cur.fetchall()

    if all_invoices:
        components.append({
            "type": "table", "id": "all-invoices", "title": "All Invoices",
            "columns": [
                {"name": "Invoice #"}, {"name": "Customer"}, {"name": "Amount"},
                {"name": "Status", "badges": {
                    "Paid": "green", "Unpaid": "red", "Partial": "yellow", "Overdue": "red",
                }},
                {"name": "Due Date"}, {"name": "Created"},
            ],
            "rows": [
                [inv, cust or "-", _fmt_amount(amt), status.title(),
                 str(due)[:10] if due else "-", str(created)[:10]]
                for inv, cust, amt, status, due, created in all_invoices
            ],
            "filterable_columns": [3],
            "page_size": 20,
        })

    # Overdue
    cur.execute(
        """SELECT i.invoice_number, o.customer_name, i.total_amount, i.due_date
           FROM invoices i
           LEFT JOIN orders o ON i.order_id = o.id
           WHERE i.payment_status = 'unpaid' AND i.due_date < NOW()
           AND i.created_at >= %s AND i.created_at < %s
           ORDER BY i.due_date ASC""",
        (from_date, to_date),
    )
    overdue = cur.fetchall()

    if overdue:
        components.append({
            "type": "text", "title": "Overdue Invoices",
            "content": f"**{len(overdue)}** invoices are past their due date and remain unpaid.",
        })
        components.append({
            "type": "table", "id": "overdue-invoices", "title": "Overdue Invoice Details",
            "columns": [{"name": "Invoice #"}, {"name": "Customer"}, {"name": "Amount"}, {"name": "Due Date"}],
            "rows": [
                [inv, cust or "-", _fmt_amount(amt), str(due)[:10] if due else "-"]
                for inv, cust, amt, due in overdue
            ],
            "page_size": 20,
        })

    return components, f"Invoice Summary • {from_date} to {to_date}"


REPORT_BUILDERS = {
    "sales": build_sales_report,
    "stock": build_stock_report,
    "invoice_summary": build_invoice_report,
}
