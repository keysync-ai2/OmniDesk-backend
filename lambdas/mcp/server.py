"""Lambda: omnidesk-mcp-server
POST /mcp
MCP JSON-RPC server — handles initialize, ping, tools/list, tools/call.
Stateless Lambda implementation (no SSE).

Auth flow:
  - JWT token is configured in Claude Desktop's mcp-remote headers
  - Every tools/call extracts user from the Authorization header
  - If token is expired, user must regenerate via /api/auth/login and update config
  - Token expiry: 48 hours (configurable in jwt_helper.py)
"""
from datetime import datetime, timezone, timedelta
import string
import random
import json
import os
import boto3
from pinecone import Pinecone
from utils.db import get_connection
from utils.jwt_helper import verify_token
from utils.audit import log_action
from utils.pdf_builder import build_invoice_pdf

SERVER_INFO = {
    "name": "omnidesk-mcp",
    "version": "3.0.0",
}

PROTOCOL_VERSION = "2025-03-26"

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, Mcp-Session-Id",
    "Access-Control-Allow-Methods": "POST, GET, DELETE, OPTIONS",
    "Access-Control-Expose-Headers": "Mcp-Session-Id",
    "Mcp-Session-Id": "lambda-stateless",
}

# ── Tool Catalog ────────────────────────────────────────────────────────

TOOLS = [
    # ── Start & Help ──────────────────────────────────────────────────
    {
        "name": "omnidesk_start",
        "description": "Login to OmniDesk. Call this FIRST when the user says 'Login OmniDesk', 'Start OmniDesk', 'Open OmniDesk', or greets you. Returns a pre-formatted markdown dashboard. IMPORTANT: Display the returned text EXACTLY as-is, preserving all markdown formatting including headers (##, ###), tables, bold text, horizontal rules, and numbered lists. Do NOT summarize, paraphrase, or rewrite the output.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "omnidesk_help",
        "description": "Show the OmniDesk help menu. Call this when the user asks 'help', 'what can you do', 'show commands', or 'how to use OmniDesk'. Returns a categorized list of all available tools with usage examples.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {"type": "string", "enum": ["all", "products", "stock", "categories", "warehouses", "orders", "invoices", "auth"], "description": "Filter help by module (default: all)"},
            },
        },
    },
    # ── Auth ──────────────────────────────────────────────────────────
    {
        "name": "get_profile",
        "description": "Get the current authenticated user's profile including name, email, role, and phone number.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ── Categories ────────────────────────────────────────────────────
    {
        "name": "category_list",
        "description": "List all product categories. Use this when user asks to see categories, browse product types, or before creating a product to find the right category.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "category_create",
        "description": "Create a new product category (e.g., 'Electronics', 'Clothing', 'Footwear'). Requires manager or admin role. Use category_list first to avoid duplicates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Category name"},
                "description": {"type": "string", "description": "Category description (optional)"},
            },
            "required": ["name"],
        },
    },
    # Products
    {
        "name": "product_list",
        "description": "List all products with pagination. Supports filtering by category and keyword search on name/SKU. Use this for browsing inventory or finding products by exact name. For natural language queries like 'comfortable shirts', use product_search instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number (default 1)"},
                "limit": {"type": "integer", "description": "Items per page (default 20, max 100)"},
                "category_id": {"type": "string", "description": "Filter by category UUID"},
                "search": {"type": "string", "description": "Search by product name or SKU"},
            },
        },
    },
    {
        "name": "product_get",
        "description": "Get a single product by ID with full details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "product_create",
        "description": "Create a new product with SKU, name, price, and optional category. Example: 'Add Blue T-Shirt, SKU BT001, ₹499 in Clothing category'. Requires manager or admin role. The product is automatically indexed for semantic search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "Stock Keeping Unit code (unique)"},
                "name": {"type": "string", "description": "Product name"},
                "description": {"type": "string", "description": "Product description"},
                "category_id": {"type": "string", "description": "Category UUID (optional)"},
                "unit_price": {"type": "number", "description": "Price per unit"},
                "unit": {"type": "string", "description": "Unit of measure (default: pcs)"},
                "extra_fields": {"type": "object", "description": "Any additional product attributes (e.g. origin, weight, supplier, license, expiry) stored in DynamoDB extended data"},
            },
            "required": ["sku", "name", "unit_price"],
        },
    },
    {
        "name": "product_update",
        "description": "Update product fields (name, description, price, category). Requires manager or admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID"},
                "name": {"type": "string", "description": "New product name"},
                "description": {"type": "string", "description": "New description"},
                "unit_price": {"type": "number", "description": "New unit price"},
                "category_id": {"type": "string", "description": "New category UUID"},
                "unit": {"type": "string", "description": "New unit of measure"},
                "extra_fields": {"type": "object", "description": "Additional attributes to update in DynamoDB extended data (e.g. origin, weight, supplier)"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "product_deactivate",
        "description": "Soft-delete a product (sets is_active=false). Requires admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID to deactivate"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "product_search",
        "description": "Smart search for products using natural language. Finds products by meaning, not just keywords. Examples: 'comfortable cotton clothing', 'something for running', 'affordable shirts'. Use this instead of product_list when the user describes what they want in natural language.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results to return (default 10, max 50)"},
            },
            "required": ["query"],
        },
    },
    # Warehouses
    {
        "name": "warehouse_list",
        "description": "List all active warehouses.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "warehouse_create",
        "description": "Create a new warehouse. Requires admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Warehouse name"},
                "address": {"type": "string", "description": "Warehouse address (optional)"},
            },
            "required": ["name"],
        },
    },
    # Stock
    {
        "name": "stock_check",
        "description": "Check how many units of a product are in stock. Shows total across all warehouses and per-warehouse breakdown. Example: 'How many Blue T-Shirts do we have?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID"},
                "warehouse_id": {"type": "string", "description": "Warehouse UUID (optional, for specific warehouse)"},
            },
            "required": ["product_id"],
        },
    },
    {
        "name": "stock_adjust",
        "description": "Add, deduct, or set stock quantity for a product in a specific warehouse. Examples: 'Add 100 Blue T-Shirts to Main Warehouse', 'Deduct 5 units, sold today'. Always logs the movement with reason. Requires staff role or higher.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID"},
                "warehouse_id": {"type": "string", "description": "Warehouse UUID"},
                "movement_type": {"type": "string", "enum": ["add", "deduct", "adjust"], "description": "Type: add (increase), deduct (decrease), adjust (set absolute)"},
                "quantity": {"type": "integer", "description": "Quantity to add/deduct/set (positive number)"},
                "reason": {"type": "string", "description": "Reason for adjustment (optional)"},
            },
            "required": ["product_id", "warehouse_id", "movement_type", "quantity"],
        },
    },
    {
        "name": "stock_low_alerts",
        "description": "Show products running low on stock (below their threshold). Use this for inventory alerts, reorder planning, or when user asks 'what needs restocking?'",
        "inputSchema": {
            "type": "object",
            "properties": {
                "warehouse_id": {"type": "string", "description": "Filter by warehouse UUID (optional)"},
            },
        },
    },
    {
        "name": "stock_movements",
        "description": "Get stock movement history for a product. Requires manager role or higher.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "Product UUID"},
                "warehouse_id": {"type": "string", "description": "Filter by warehouse UUID (optional)"},
                "page": {"type": "integer", "description": "Page number (default 1)"},
                "limit": {"type": "integer", "description": "Items per page (default 50)"},
            },
            "required": ["product_id"],
        },
    },
    # ── Orders ────────────────────────────────────────────────────────
    {
        "name": "order_create",
        "description": "Create a new order with line items. Example: 'Create order for Rahul — 3 Blue T-Shirts and 2 Black Sneakers'. Auto-calculates totals. Stock is NOT deducted yet — only when order is confirmed. Requires staff role or higher.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer name"},
                "customer_email": {"type": "string", "description": "Customer email (optional)"},
                "customer_phone": {"type": "string", "description": "Customer phone (optional)"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "string", "description": "Product UUID"},
                            "quantity": {"type": "integer", "description": "Quantity"},
                        },
                        "required": ["product_id", "quantity"],
                    },
                    "description": "List of products and quantities",
                },
                "notes": {"type": "string", "description": "Order notes (optional)"},
            },
            "required": ["customer_name", "items"],
        },
    },
    {
        "name": "order_list",
        "description": "List orders with pagination. Filter by status (pending/confirmed/shipped/delivered/cancelled), date range, or search by customer name/order number. Example: 'Show me all pending orders'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "confirmed", "shipped", "delivered", "cancelled"], "description": "Filter by status"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "search": {"type": "string", "description": "Search by customer name or order number"},
                "page": {"type": "integer", "description": "Page number (default 1)"},
                "limit": {"type": "integer", "description": "Items per page (default 20)"},
            },
        },
    },
    {
        "name": "order_get",
        "description": "Get a single order with full details including all line items, product names, and pricing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "order_update_status",
        "description": "Update order status. Valid transitions: pending→confirmed→shipped→delivered. On 'confirmed', stock is automatically deducted. Requires staff role or higher. To cancel, use order_cancel instead.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID"},
                "status": {"type": "string", "enum": ["confirmed", "shipped", "delivered"], "description": "New status"},
                "warehouse_id": {"type": "string", "description": "Warehouse for stock deduction (needed when confirming, defaults to first warehouse)"},
            },
            "required": ["order_id", "status"],
        },
    },
    {
        "name": "order_cancel",
        "description": "Cancel an order (admin only). Has a confirmation gate — first call shows a preview, second call with confirm:true actually cancels. If order was confirmed, stock is automatically restored.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID"},
                "confirm": {"type": "boolean", "description": "Set to true to confirm cancellation (required on second call)"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "order_history",
        "description": "Get the full status change history for an order. Shows who changed what and when.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID"},
            },
            "required": ["order_id"],
        },
    },
    # ── Invoices ──────────────────────────────────────────────────────
    {
        "name": "invoice_generate",
        "description": "Generate a professional PDF invoice from an order. Supports multi-currency, configurable tax (GST/VAT/Sales Tax), and customizable company info. Uses org settings as defaults with per-invoice overrides. Example: 'Generate invoice for Order ORD-20260309-A3F7 with 18% GST'. Requires manager role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Order UUID to generate invoice for"},
                "tax_rate": {"type": "number", "description": "Tax rate percentage (e.g., 18 for 18% GST, 20 for 20% VAT, default 0)"},
                "due_date": {"type": "string", "description": "Due date YYYY-MM-DD (default: 30 days from now)"},
                "notes": {"type": "string", "description": "Additional notes to include on the invoice"},
                "currency_symbol": {"type": "string", "description": "Override currency symbol (e.g., '$', '€', '£'). Uses org default if not provided."},
                "tax_label": {"type": "string", "description": "Override tax label (e.g., 'VAT', 'Sales Tax'). Uses org default if not provided."},
                "payment_terms": {"type": "string", "description": "Override payment terms (e.g., 'Due on receipt', 'Net 15'). Uses org default if not provided."},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "invoice_list",
        "description": "List invoices with pagination. Filter by payment status (unpaid/paid/overdue/partial) or date range. Example: 'Show me all unpaid invoices'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "payment_status": {"type": "string", "enum": ["unpaid", "paid", "overdue", "partial"], "description": "Filter by payment status"},
                "from_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "to_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "search": {"type": "string", "description": "Search by invoice number, customer, or order number"},
                "page": {"type": "integer", "description": "Page number (default 1)"},
                "limit": {"type": "integer", "description": "Items per page (default 20)"},
            },
        },
    },
    {
        "name": "invoice_get",
        "description": "Get full details of a single invoice including order info and amounts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice UUID"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "invoice_download",
        "description": "Get a download link for an invoice. Returns a time-limited URL (15 minutes) to view/print the invoice.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice UUID"},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "invoice_send",
        "description": "Send an invoice to the customer. Currently provides a download link (email delivery coming in Phase 4). Marks the invoice as 'sent'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "Invoice UUID"},
            },
            "required": ["invoice_id"],
        },
    },
    # ── Org Settings ──────────────────────────────────────────
    {
        "name": "org_settings_get",
        "description": "View organization settings (company info, currency, tax label, invoice defaults). Example: 'Show org settings' or 'What currency are invoices in?'.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "org_settings_update",
        "description": "Update organization settings for invoice customization. Settings: company_name, company_address, company_phone, company_email, currency_code, currency_symbol, tax_label (GST/VAT/Sales Tax), payment_terms, invoice_footer, locale. Example: 'Change currency to USD ($)' or 'Set company name to Acme Corp'. Requires admin role.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "settings": {
                    "type": "object",
                    "description": "Key-value pairs to update. Keys: company_name, company_address, company_phone, company_email, currency_code, currency_symbol, tax_label, payment_terms, invoice_footer, locale",
                },
            },
            "required": ["settings"],
        },
    },
]

# ── RBAC ───────────────────────────────────────────────────────────────

ROLE_HIERARCHY = {"admin": 4, "manager": 3, "staff": 2, "viewer": 1}

# Tool name → minimum role required
TOOL_ROLES = {
    "omnidesk_start": "viewer",
    "omnidesk_help": "viewer",
    "get_profile": "viewer",
    "category_list": "viewer",
    "category_create": "manager",
    "product_list": "viewer",
    "product_get": "viewer",
    "product_create": "manager",
    "product_update": "manager",
    "product_deactivate": "admin",
    "product_search": "viewer",
    "warehouse_list": "viewer",
    "warehouse_create": "admin",
    "stock_check": "viewer",
    "stock_adjust": "staff",
    "stock_low_alerts": "viewer",
    "stock_movements": "manager",
    "order_create": "staff",
    "order_list": "viewer",
    "order_get": "viewer",
    "order_update_status": "staff",
    "order_cancel": "admin",
    "order_history": "viewer",
    "invoice_generate": "manager",
    "invoice_list": "viewer",
    "invoice_get": "viewer",
    "invoice_download": "viewer",
    "invoice_send": "manager",
    "org_settings_get": "viewer",
    "org_settings_update": "admin",
}


def check_role(user, tool_name):
    min_role = TOOL_ROLES.get(tool_name, "viewer")
    user_level = ROLE_HIERARCHY.get(user.get("role", "viewer"), 1)
    required_level = ROLE_HIERARCHY.get(min_role, 1)
    return user_level >= required_level


# ── Tool Handlers ───────────────────────────────────────────────────────


def handle_omnidesk_start(args, user=None):
    """Dashboard: profile + low stock alerts + product count + recent summary."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        # Profile
        cur.execute(
            "SELECT id, email, full_name, phone, role, created_at FROM users WHERE id = %s AND is_active = TRUE",
            (user["user_id"],),
        )
        profile_row = cur.fetchone()
        if not profile_row:
            return {"error": "User not found or deactivated"}

        profile = {
            "user_id": str(profile_row[0]), "email": profile_row[1],
            "full_name": profile_row[2], "phone": profile_row[3],
            "role": profile_row[4], "member_since": str(profile_row[5]),
        }

        # Quick stats
        cur.execute("SELECT COUNT(*) FROM products WHERE is_active = TRUE")
        total_products = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM categories WHERE is_active = TRUE")
        total_categories = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM warehouses WHERE is_active = TRUE")
        total_warehouses = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM orders WHERE status NOT IN ('cancelled', 'delivered')")
        active_orders = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM invoices WHERE payment_status = 'unpaid'")
        unpaid_invoices = cur.fetchone()[0]

        # Low stock alerts
        cur.execute(
            """SELECT p.name, p.sku, s.quantity, s.low_stock_threshold, w.name
               FROM stock s JOIN products p ON s.product_id = p.id JOIN warehouses w ON s.warehouse_id = w.id
               WHERE s.quantity < s.low_stock_threshold AND p.is_active = TRUE
               ORDER BY (s.low_stock_threshold - s.quantity) DESC LIMIT 5"""
        )
        low_stock = [
            {"product": r[0], "sku": r[1], "quantity": r[2],
                "threshold": r[3], "warehouse": r[4]}
            for r in cur.fetchall()
        ]

        # Build formatted markdown response (Design 4 — Conversational + Structured)
        name = profile["full_name"] or "there"
        lines = []

        lines.append(f"## Welcome back, {name}!")
        lines.append("")
        lines.append(
            f"You're logged in as **{profile['email']}** ({profile['role']}).")
        lines.append("")
        lines.append("Here's what's happening in your business right now:")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Business snapshot
        lines.append("### Your Numbers")
        lines.append("")
        lines.append("| | Count |")
        lines.append("|---|------:|")
        lines.append(f"| Products in catalog | {total_products} |")
        lines.append(f"| Product categories | {total_categories} |")
        lines.append(f"| Warehouses | {total_warehouses} |")
        lines.append(f"| Orders in progress | {active_orders} |")
        lines.append(f"| Invoices awaiting payment | {unpaid_invoices} |")
        lines.append("")

        # Low stock alerts
        if low_stock:
            lines.append("### Needs Attention")
            lines.append("")
            lines.append("These items are running low on stock:")
            lines.append("")
            lines.append("| Product | In Stock | Min Required |")
            lines.append("|---------|----------|--------------|")
            for item in low_stock:
                lines.append(
                    f"| {item['product']} | {item['quantity']} units | {item['threshold']} units |")
            lines.append("")
            lines.append(
                '*Consider restocking soon — say "Add [qty] units of [product] to [warehouse]"*')
            lines.append("")
        else:
            lines.append("### Stock Status")
            lines.append("")
            lines.append("All products are well stocked. No alerts right now.")
            lines.append("")

        lines.append("---")
        lines.append("")

        # Quick actions
        lines.append("### What can I help with?")
        lines.append("")
        lines.append("**Most used commands:**")
        lines.append('1. "Show all products"')
        lines.append('2. "Create order for [customer] — [items]"')
        lines.append('3. "Show low stock alerts"')
        lines.append('4. "Generate invoice for [order]"')
        lines.append('5. "Search for [keyword]"')
        lines.append("")
        lines.append(
            '**Need more?** Just say "help" for the full command menu across all 7 modules.')
        lines.append(
            'Instructions: Display above details as it is without changing or updating anything. DO NOT FORMAT IT.')

        return {"text": "\n".join(lines)}
    finally:
        conn.close()


def handle_omnidesk_help(args, user=None):
    """Return categorized help menu with usage examples."""
    module_filter = (args.get("module") or "all").lower()

    help_sections = {
        "auth": {
            "title": "Auth & Profile",
            "tools": [
                {"command": "omnidesk_start", "description": "Login dashboard — your profile, stock alerts, quick stats",
                    "example": "Login OmniDesk"},
                {"command": "get_profile", "description": "View your profile details",
                    "example": "Show my profile"},
            ],
        },
        "categories": {
            "title": "Categories",
            "tools": [
                {"command": "category_list", "description": "List all product categories",
                    "example": "Show me all categories"},
                {"command": "category_create",
                    "description": "Create a new category (manager+)", "example": "Create category 'Electronics'"},
            ],
        },
        "products": {
            "title": "Products",
            "tools": [
                {"command": "product_list", "description": "Browse products with filters",
                    "example": "Show all products in Clothing"},
                {"command": "product_get", "description": "Get full details of a product",
                    "example": "Show details of Blue T-Shirt"},
                {"command": "product_create",
                    "description": "Add a new product (manager+)", "example": "Add product Red Polo, SKU RP001, ₹699"},
                {"command": "product_update",
                    "description": "Update product fields (manager+)", "example": "Change Blue T-Shirt price to ₹549"},
                {"command": "product_deactivate",
                    "description": "Remove a product (admin only)", "example": "Deactivate product Wireless Mouse"},
                {"command": "product_search", "description": "Smart search by description",
                    "example": "Find comfortable cotton clothing"},
            ],
        },
        "warehouses": {
            "title": "Warehouses",
            "tools": [
                {"command": "warehouse_list", "description": "List all warehouses",
                    "example": "Show me all warehouses"},
                {"command": "warehouse_create",
                    "description": "Add a new warehouse (admin only)", "example": "Create warehouse 'Mumbai Hub'"},
            ],
        },
        "stock": {
            "title": "Stock Management",
            "tools": [
                {"command": "stock_check", "description": "Check stock level for a product",
                    "example": "How many Blue T-Shirts in stock?"},
                {"command": "stock_adjust",
                    "description": "Add/deduct/set stock (staff+)", "example": "Add 50 Blue T-Shirts to Main Warehouse"},
                {"command": "stock_low_alerts", "description": "Products running low on stock",
                    "example": "What needs restocking?"},
                {"command": "stock_movements",
                    "description": "Stock change history (manager+)", "example": "Show stock history for Blue T-Shirt"},
            ],
        },
        "orders": {
            "title": "Order Management",
            "tools": [
                {"command": "order_create",
                    "description": "Create a new order (staff+)", "example": "Create order for Rahul — 3 Blue T-Shirts"},
                {"command": "order_list", "description": "List/search orders",
                    "example": "Show all pending orders"},
                {"command": "order_get", "description": "Get order details with items",
                    "example": "Show details of Order ORD-20260309-A3F7"},
                {"command": "order_update_status",
                    "description": "Update order status (staff+)", "example": "Mark order as confirmed"},
                {"command": "order_cancel",
                    "description": "Cancel an order (admin only)", "example": "Cancel Order ORD-20260309-A3F7"},
                {"command": "order_history", "description": "View order status timeline",
                    "example": "Show history for this order"},
            ],
        },
        "invoices": {
            "title": "Invoices",
            "tools": [
                {"command": "invoice_generate",
                    "description": "Generate invoice from order (manager+)", "example": "Generate invoice for this order with 18% GST"},
                {"command": "invoice_list", "description": "List/search invoices",
                    "example": "Show all unpaid invoices"},
                {"command": "invoice_get", "description": "Get invoice details",
                    "example": "Show invoice INV-20260309-B4K2"},
                {"command": "invoice_download", "description": "Get download link for invoice",
                    "example": "Download this invoice"},
                {"command": "invoice_send",
                    "description": "Send invoice to customer (manager+)", "example": "Send this invoice to the customer"},
            ],
        },
    }

    if module_filter != "all" and module_filter in help_sections:
        sections = {module_filter: help_sections[module_filter]}
    else:
        sections = help_sections

    role_info = {
        "viewer": "Can view products, stock, orders, invoices, categories",
        "staff": "Can adjust stock, create/update orders + all viewer actions",
        "manager": "Can create products, generate invoices + all staff actions",
        "admin": "Full access — cancel orders, create warehouses, deactivate products",
    }

    return {
        "help": "OmniDesk — AI-Powered Business Operations",
        "your_role": user.get("role", "viewer"),
        "role_permissions": role_info,
        "modules": sections,
        "tips": [
            "Say 'Login OmniDesk' to see your dashboard",
            "Use natural language — 'Find me something blue' works!",
            "All write actions are logged for audit",
        ],
    }


def handle_get_profile(args, user=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, full_name, phone, role, is_active, created_at FROM users WHERE id = %s",
            (user["user_id"],),
        )
        row = cur.fetchone()
        if not row or not row[5]:
            return {"error": "User not found or deactivated"}
        return {
            "user_id": str(row[0]), "email": row[1], "full_name": row[2],
            "phone": row[3], "role": row[4], "created_at": str(row[6]),
        }
    finally:
        conn.close()


def handle_category_list(args, user=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, description, created_at FROM categories WHERE is_active = TRUE ORDER BY name")
        rows = cur.fetchall()
        return {
            "categories": [{"id": str(r[0]), "name": r[1], "description": r[2], "created_at": str(r[3])} for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def handle_category_create(args, user=None):
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip() or None
    if not name:
        return {"error": "Category name is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM categories WHERE name = %s AND is_active = TRUE", (name,))
        if cur.fetchone():
            return {"error": f"Category '{name}' already exists"}
        cur.execute(
            "INSERT INTO categories (name, description) VALUES (%s, %s) RETURNING id, name, description, created_at",
            (name, description),
        )
        row = cur.fetchone()
        conn.commit()
        log_action(user["user_id"], "create_category", "categories",
                   entity_id=str(row[0]), details={"name": row[1]})
        return {"id": str(row[0]), "name": row[1], "description": row[2], "created_at": str(row[3])}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_product_list(args, user=None):
    page = max(int(args.get("page", 1)), 1)
    limit = min(max(int(args.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    category_id = args.get("category_id")
    search = (args.get("search") or "").strip()

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
        cur.execute(f"SELECT COUNT(*) FROM products p WHERE {where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"""SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name,
                       p.unit_price, p.unit, p.created_at
                FROM products p LEFT JOIN categories c ON p.category_id = c.id
                WHERE {where} ORDER BY p.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()
        return {
            "products": [{
                "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
                "category_id": str(r[4]) if r[4] else None, "category_name": r[5],
                "unit_price": str(r[6]), "unit": r[7], "created_at": str(r[8]),
            } for r in rows],
            "total": total, "page": page, "limit": limit,
        }
    finally:
        conn.close()


def handle_product_get(args, user=None):
    product_id = args.get("product_id")
    if not product_id:
        return {"error": "product_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name,
                      p.unit_price, p.unit, p.is_active, p.created_by, p.created_at, p.updated_at,
                      p.extra_fields
               FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.id = %s""",
            (product_id,),
        )
        r = cur.fetchone()
        if not r:
            return {"error": "Product not found"}
        if not r[8]:
            return {"error": "Product has been deactivated"}
        ef = r[12] if isinstance(r[12], dict) else json.loads(r[12] or '{}')
        result = {
            "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "category_name": r[5],
            "unit_price": str(r[6]), "unit": r[7], "created_by": str(r[9]),
            "created_at": str(r[10]), "updated_at": str(r[11]),
            "extra_fields": ef,
        }
        return result
    finally:
        conn.close()


def handle_product_create(args, user=None):
    sku = (args.get("sku") or "").strip().upper()
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip() or None
    category_id = args.get("category_id")
    unit_price = args.get("unit_price")
    unit = (args.get("unit") or "pcs").strip().lower()
    extra_fields = args.get("extra_fields") or {}

    if not sku:
        return {"error": "SKU is required"}
    if not name:
        return {"error": "Product name is required"}
    if unit_price is None:
        return {"error": "unit_price is required"}
    try:
        unit_price = float(unit_price)
        if unit_price < 0:
            raise ValueError
    except (ValueError, TypeError):
        return {"error": "unit_price must be a non-negative number"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        if category_id:
            cur.execute(
                "SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (category_id,))
            if not cur.fetchone():
                return {"error": "Category not found"}
        cur.execute("SELECT id FROM products WHERE sku = %s", (sku,))
        if cur.fetchone():
            return {"error": f"Product with SKU '{sku}' already exists"}

        cur.execute(
            """INSERT INTO products (sku, name, description, category_id, unit_price, unit, extra_fields, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, sku, name, description, category_id, unit_price, unit, extra_fields, created_at""",
            (sku, name, description, category_id,
             unit_price, unit, json.dumps(extra_fields) if extra_fields else '{}', user["user_id"]),
        )
        r = cur.fetchone()
        product_id = str(r[0])
        conn.commit()

        log_action(user["user_id"], "create_product", "products", entity_id=product_id,
                   details={"sku": r[1], "name": r[2], "unit_price": str(r[5])})

        # Index in Pinecone with extra fields for semantic search
        _upsert_pinecone(product_id, r[2], r[3], r[1], r[6], str(r[5]), extra_fields)

        ef = r[7] if isinstance(r[7], dict) else json.loads(r[7] or '{}')
        return {
            "id": product_id, "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "unit_price": str(r[5]),
            "unit": r[6], "extra_fields": ef, "created_at": str(r[8]),
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_product_update(args, user=None):
    product_id = args.get("product_id")
    if not product_id:
        return {"error": "product_id is required"}

    allowed = {"name": str, "description": str,
               "category_id": str, "unit_price": float, "unit": str}
    updates = []
    params = []
    for field, cast in allowed.items():
        if field in args:
            val = args[field]
            if val is not None:
                try:
                    val = cast(val) if val != "" else None
                except (ValueError, TypeError):
                    return {"error": f"Invalid value for {field}"}
            updates.append(f"{field} = %s")
            params.append(val)

    extra_fields = args.get("extra_fields") or {}

    if not updates and not extra_fields:
        return {"error": "No fields to update"}

    # Merge extra_fields into JSONB column
    if extra_fields:
        updates.append("extra_fields = COALESCE(extra_fields, '{}'::jsonb) || %s::jsonb")
        params.append(json.dumps(extra_fields))

    updates.append("updated_at = NOW()")
    params.append(product_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_active FROM products WHERE id = %s",
                    (product_id,))
        row = cur.fetchone()
        if not row:
            return {"error": "Product not found"}
        if not row[0]:
            return {"error": "Product has been deactivated"}

        if "category_id" in args and args["category_id"]:
            cur.execute(
                "SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (args["category_id"],))
            if not cur.fetchone():
                return {"error": "Category not found"}

        set_clause = ", ".join(updates)
        cur.execute(
            f"""UPDATE products SET {set_clause} WHERE id = %s AND is_active = TRUE
                RETURNING id, sku, name, description, category_id, unit_price, unit, updated_at, extra_fields""",
            params,
        )
        r = cur.fetchone()
        if not r:
            return {"error": "Product not found or deactivated"}
        conn.commit()
        log_action(user["user_id"], "update_product", "products", entity_id=product_id,
                   details={k: str(args[k]) for k in args if k in allowed})

        ef = r[8] if isinstance(r[8], dict) else json.loads(r[8] or '{}')
        _upsert_pinecone(product_id, r[2], r[3], r[1], r[6], str(r[5]), ef if ef else None)

        return {
            "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "unit_price": str(r[5]),
            "unit": r[6], "updated_at": str(r[7]), "extra_fields": ef,
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_product_deactivate(args, user=None):
    product_id = args.get("product_id")
    if not product_id:
        return {"error": "product_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE products SET is_active = FALSE, updated_at = NOW() WHERE id = %s AND is_active = TRUE RETURNING id, name",
            (product_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Product not found or already deactivated"}
        conn.commit()
        log_action(user["user_id"], "deactivate_product", "products",
                   entity_id=product_id, details={"name": row[1]})
        return {"message": f"Product '{row[1]}' deactivated", "id": str(row[0])}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_warehouse_list(args, user=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, address, created_at FROM warehouses WHERE is_active = TRUE ORDER BY name")
        rows = cur.fetchall()
        return {
            "warehouses": [{"id": str(r[0]), "name": r[1], "address": r[2], "created_at": str(r[3])} for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def handle_warehouse_create(args, user=None):
    name = (args.get("name") or "").strip()
    address = (args.get("address") or "").strip() or None
    if not name:
        return {"error": "Warehouse name is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO warehouses (name, address) VALUES (%s, %s) RETURNING id, name, address, created_at",
            (name, address),
        )
        row = cur.fetchone()
        conn.commit()
        log_action(user["user_id"], "create_warehouse", "warehouses",
                   entity_id=str(row[0]), details={"name": row[1]})
        return {"id": str(row[0]), "name": row[1], "address": row[2], "created_at": str(row[3])}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_stock_check(args, user=None):
    product_id = args.get("product_id")
    if not product_id:
        return {"error": "product_id is required"}

    warehouse_id = args.get("warehouse_id")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, sku FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": "Product not found"}

        if warehouse_id:
            cur.execute(
                """SELECT s.quantity, s.low_stock_threshold, w.name, w.id
                   FROM stock s JOIN warehouses w ON s.warehouse_id = w.id
                   WHERE s.product_id = %s AND s.warehouse_id = %s""",
                (product_id, warehouse_id),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                    "quantity": 0, "low_stock_threshold": 10, "is_low_stock": True,
                    "warehouse_id": warehouse_id, "warehouse_name": None,
                }
            return {
                "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                "quantity": row[0], "low_stock_threshold": row[1], "is_low_stock": row[0] < row[1],
                "warehouse_id": str(row[3]), "warehouse_name": row[2],
            }
        else:
            cur.execute(
                "SELECT COALESCE(SUM(s.quantity), 0), MIN(s.low_stock_threshold) FROM stock s WHERE s.product_id = %s",
                (product_id,),
            )
            agg = cur.fetchone()
            total_qty = agg[0]
            threshold = agg[1] or 10

            cur.execute(
                """SELECT w.id, w.name, s.quantity, s.low_stock_threshold
                   FROM stock s JOIN warehouses w ON s.warehouse_id = w.id
                   WHERE s.product_id = %s ORDER BY w.name""",
                (product_id,),
            )
            warehouses = [
                {"warehouse_id": str(
                    r[0]), "warehouse_name": r[1], "quantity": r[2], "low_stock_threshold": r[3]}
                for r in cur.fetchall()
            ]
            return {
                "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
                "total_quantity": total_qty, "low_stock_threshold": threshold,
                "is_low_stock": total_qty < threshold, "warehouses": warehouses,
            }
    finally:
        conn.close()


def handle_stock_adjust(args, user=None):
    product_id = args.get("product_id")
    warehouse_id = args.get("warehouse_id")
    movement_type = (args.get("movement_type") or "").strip().lower()
    quantity = args.get("quantity")
    reason = (args.get("reason") or "").strip() or None

    if not product_id:
        return {"error": "product_id is required"}
    if not warehouse_id:
        return {"error": "warehouse_id is required"}
    if movement_type not in {"add", "deduct", "adjust"}:
        return {"error": "movement_type must be add, deduct, or adjust"}
    try:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return {"error": "quantity must be a positive integer"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": "Product not found"}

        cur.execute(
            "SELECT id, name FROM warehouses WHERE id = %s AND is_active = TRUE", (warehouse_id,))
        warehouse = cur.fetchone()
        if not warehouse:
            return {"error": "Warehouse not found"}

        cur.execute("SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s",
                    (product_id, warehouse_id))
        stock_row = cur.fetchone()
        current_qty = stock_row[1] if stock_row else 0

        if movement_type == "add":
            new_qty = current_qty + quantity
        elif movement_type == "deduct":
            new_qty = current_qty - quantity
            if new_qty < 0:
                return {"error": f"Insufficient stock. Current: {current_qty}, Requested deduction: {quantity}"}
        else:
            new_qty = quantity

        if stock_row:
            cur.execute(
                "UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)",
                        (product_id, warehouse_id, new_qty))

        cur.execute(
            """INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (product_id, warehouse_id, movement_type,
             quantity, reason, user["user_id"]),
        )
        movement = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], f"stock_{movement_type}", "stock", entity_id=product_id,
                   details={"warehouse_id": warehouse_id, "quantity": quantity, "new_total": new_qty, "reason": reason})

        return {
            "product_id": str(product[0]), "product_name": product[1],
            "warehouse_id": str(warehouse[0]), "warehouse_name": warehouse[1],
            "movement_type": movement_type, "quantity_changed": quantity,
            "previous_quantity": current_qty, "new_quantity": new_qty,
            "movement_id": str(movement[0]), "reason": reason, "created_at": str(movement[1]),
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_stock_low_alerts(args, user=None):
    warehouse_id = args.get("warehouse_id")

    conn = get_connection()
    try:
        cur = conn.cursor()
        if warehouse_id:
            cur.execute(
                """SELECT p.id, p.sku, p.name, s.quantity, s.low_stock_threshold, w.id, w.name
                   FROM stock s JOIN products p ON s.product_id = p.id JOIN warehouses w ON s.warehouse_id = w.id
                   WHERE s.quantity < s.low_stock_threshold AND p.is_active = TRUE AND s.warehouse_id = %s
                   ORDER BY (s.low_stock_threshold - s.quantity) DESC""",
                (warehouse_id,),
            )
        else:
            cur.execute(
                """SELECT p.id, p.sku, p.name, s.quantity, s.low_stock_threshold, w.id, w.name
                   FROM stock s JOIN products p ON s.product_id = p.id JOIN warehouses w ON s.warehouse_id = w.id
                   WHERE s.quantity < s.low_stock_threshold AND p.is_active = TRUE
                   ORDER BY (s.low_stock_threshold - s.quantity) DESC"""
            )
        rows = cur.fetchall()
        return {
            "low_stock_products": [{
                "product_id": str(r[0]), "sku": r[1], "product_name": r[2],
                "quantity": r[3], "low_stock_threshold": r[4], "deficit": r[4] - r[3],
                "warehouse_id": str(r[5]), "warehouse_name": r[6],
            } for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def handle_stock_movements(args, user=None):
    product_id = args.get("product_id")
    if not product_id:
        return {"error": "product_id is required"}

    warehouse_id = args.get("warehouse_id")
    page = max(int(args.get("page", 1)), 1)
    limit = min(max(int(args.get("limit", 50)), 1), 100)
    offset = (page - 1) * limit

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, sku FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": "Product not found"}

        conditions = ["sm.product_id = %s"]
        params = [product_id]
        if warehouse_id:
            conditions.append("sm.warehouse_id = %s")
            params.append(warehouse_id)
        where = " AND ".join(conditions)

        cur.execute(
            f"SELECT COUNT(*) FROM stock_movements sm WHERE {where}", params)
        total = cur.fetchone()[0]

        cur.execute(
            f"""SELECT sm.id, sm.movement_type, sm.quantity, sm.reason,
                       sm.performed_by, u.full_name, sm.created_at, w.id, w.name
                FROM stock_movements sm
                LEFT JOIN users u ON sm.performed_by = u.id
                LEFT JOIN warehouses w ON sm.warehouse_id = w.id
                WHERE {where} ORDER BY sm.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()
        return {
            "product_id": str(product[0]), "product_name": product[1], "sku": product[2],
            "movements": [{
                "id": str(r[0]), "movement_type": r[1], "quantity": r[2], "reason": r[3],
                "performed_by": str(r[4]) if r[4] else None, "performed_by_name": r[5],
                "created_at": str(r[6]), "warehouse_id": str(r[7]) if r[7] else None, "warehouse_name": r[8],
            } for r in rows],
            "total": total, "page": page, "limit": limit,
        }
    finally:
        conn.close()


_pc_client = None
_pc_index = None


def _get_pinecone_index():
    """Get Pinecone index (cached per Lambda container)."""
    global _pc_client, _pc_index
    if _pc_index:
        return _pc_index
    secret_name = os.environ.get("PINECONE_SECRET_ARN", "omnidesk/pinecone")
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    secret = json.loads(sm.get_secret_value(
        SecretId=secret_name)["SecretString"])
    _pc_client = Pinecone(api_key=secret["api_key"])
    _pc_index = _pc_client.Index("omnidesk-products")
    return _pc_index


def _upsert_pinecone(product_id, name, description, sku, unit, unit_price, extra_fields=None):
    """Best-effort upsert product into Pinecone with extra fields."""
    try:
        index = _get_pinecone_index()
        parts = [name]
        if description:
            parts.append(description)
        if sku:
            parts.append(f"SKU: {sku}")
        if unit_price:
            parts.append(f"Price: {unit_price} per {unit or 'pcs'}")
        if extra_fields and isinstance(extra_fields, dict):
            for k, v in extra_fields.items():
                if v:
                    parts.append(f"{k}: {v}")
        product_text = " | ".join(parts)
        index.upsert_records(namespace="products", records=[{
            "_id": str(product_id),
            "product_text": product_text,
        }])
    except Exception as e:
        print(f"[mcp] Pinecone upsert failed for {product_id}: {e}")


def handle_product_search(args, user=None):
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "query is required"}

    top_k = min(max(int(args.get("top_k", 10)), 1), 50)

    try:
        index = _get_pinecone_index()
        results = index.search(
            namespace="products",
            query={"inputs": {"text": query}, "top_k": top_k},
            fields=["product_text"],
        )
        # Response is a Pinecone SDK object — use attribute access
        hits = results.result.hits if hasattr(results, 'result') else []
        if not hits:
            return {"products": [], "total": 0, "query": query}

        score_map = {h["_id"]: h.get("_score", 0) for h in hits}
        product_ids = sorted([h["_id"] for h in hits],
                             key=lambda pid: score_map.get(pid, 0), reverse=True)

        conn = get_connection()
        try:
            cur = conn.cursor()
            placeholders = ", ".join(["%s"] * len(product_ids))
            cur.execute(
                f"""SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name,
                           p.unit_price, p.unit, p.created_at
                    FROM products p LEFT JOIN categories c ON p.category_id = c.id
                    WHERE p.id::text IN ({placeholders}) AND p.is_active = TRUE""",
                product_ids,
            )
            rows = cur.fetchall()
            products_by_id = {}
            for r in rows:
                pid = str(r[0])
                products_by_id[pid] = {
                    "id": pid, "sku": r[1], "name": r[2], "description": r[3],
                    "category_id": str(r[4]) if r[4] else None, "category_name": r[5],
                    "unit_price": str(r[6]), "unit": r[7], "created_at": str(r[8]),
                    "relevance_score": round(score_map.get(pid, 0), 4),
                }
            ordered = [products_by_id[pid]
                       for pid in product_ids if pid in products_by_id]
            return {"products": ordered, "total": len(ordered), "query": query}
        finally:
            conn.close()
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}


# ── Order Handlers ─────────────────────────────────────────────────────


VALID_TRANSITIONS = {
    "pending": ["confirmed", "cancelled"],
    "confirmed": ["shipped", "cancelled"],
    "shipped": ["delivered"],
    "delivered": [],
    "cancelled": [],
}


def _generate_number(prefix):
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    rand_part = "".join(random.choices(
        string.ascii_uppercase + string.digits, k=4))
    return f"{prefix}-{date_part}-{rand_part}"


def handle_order_create(args, user=None):
    customer_name = (args.get("customer_name") or "").strip()
    customer_email = (args.get("customer_email") or "").strip() or None
    customer_phone = (args.get("customer_phone") or "").strip() or None
    items = args.get("items") or []
    notes = (args.get("notes") or "").strip() or None

    if not customer_name:
        return {"error": "customer_name is required"}
    if not items or not isinstance(items, list):
        return {"error": "items is required (array of {product_id, quantity})"}

    for i, item in enumerate(items):
        if not item.get("product_id"):
            return {"error": f"items[{i}].product_id is required"}
        try:
            qty = int(item.get("quantity", 0))
            if qty <= 0:
                raise ValueError
            item["quantity"] = qty
        except (ValueError, TypeError):
            return {"error": f"items[{i}].quantity must be a positive integer"}

    conn = get_connection()
    try:
        cur = conn.cursor()

        product_ids = [item["product_id"] for item in items]
        placeholders = ",".join(["%s"] * len(product_ids))
        cur.execute(
            f"SELECT id, name, sku, unit_price FROM products WHERE id IN ({placeholders}) AND is_active = TRUE",
            product_ids,
        )
        products = {str(r[0]): {"name": r[1], "sku": r[2],
                                "unit_price": r[3]} for r in cur.fetchall()}

        missing = [pid for pid in product_ids if pid not in products]
        if missing:
            return {"error": f"Products not found: {', '.join(missing)}"}

        order_number = _generate_number("ORD")
        for _ in range(5):
            cur.execute(
                "SELECT id FROM orders WHERE order_number = %s", (order_number,))
            if not cur.fetchone():
                break
            order_number = _generate_number("ORD")

        subtotal = 0
        order_items_data = []
        for item in items:
            product = products[item["product_id"]]
            item_total = float(product["unit_price"]) * item["quantity"]
            subtotal += item_total
            order_items_data.append({
                "product_id": item["product_id"], "product_name": product["name"],
                "sku": product["sku"], "quantity": item["quantity"],
                "unit_price": float(product["unit_price"]), "total_price": item_total,
            })

        cur.execute(
            """INSERT INTO orders (order_number, customer_name, customer_email, customer_phone,
                   status, subtotal, total_amount, notes, created_by)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s, %s, %s)
               RETURNING id, order_number, status, created_at""",
            (order_number, customer_name, customer_email, customer_phone,
             subtotal, subtotal, notes, user["user_id"]),
        )
        order_row = cur.fetchone()
        order_id = str(order_row[0])

        result_items = []
        for oi in order_items_data:
            cur.execute(
                """INSERT INTO order_items (order_id, product_id, quantity, unit_price, total_price)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (order_id, oi["product_id"], oi["quantity"],
                 oi["unit_price"], oi["total_price"]),
            )
            result_items.append({
                "id": str(cur.fetchone()[0]), "product_name": oi["product_name"],
                "sku": oi["sku"], "quantity": oi["quantity"],
                "unit_price": oi["unit_price"], "total_price": oi["total_price"],
            })

        cur.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, changed_by) VALUES (%s, NULL, 'pending', %s)",
            (order_id, user["user_id"]),
        )
        conn.commit()

        log_action(user["user_id"], "create_order", "orders", entity_id=order_id,
                   details={"order_number": order_row[1], "customer": customer_name, "total": str(subtotal)})

        return {
            "id": order_id, "order_number": order_row[1], "customer_name": customer_name,
            "customer_email": customer_email, "customer_phone": customer_phone,
            "status": "pending", "items": result_items,
            "subtotal": str(subtotal), "total_amount": str(subtotal),
            "notes": notes, "created_at": str(order_row[3]),
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_order_list(args, user=None):
    page = max(int(args.get("page", 1)), 1)
    limit = min(max(int(args.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    status_filter = (args.get("status") or "").strip().lower() or None
    from_date = args.get("from_date")
    to_date = args.get("to_date")
    search = (args.get("search") or "").strip()

    conditions = []
    params = []
    if status_filter:
        conditions.append("o.status = %s")
        params.append(status_filter)
    if from_date:
        conditions.append("o.created_at >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("o.created_at <= %s")
        params.append(to_date)
    if search:
        conditions.append(
            "(o.customer_name ILIKE %s OR o.order_number ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM orders o {where}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT o.id, o.order_number, o.customer_name, o.customer_email,
                       o.status, o.total_amount, o.created_at
                FROM orders o {where} ORDER BY o.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()
        return {
            "orders": [{
                "id": str(r[0]), "order_number": r[1], "customer_name": r[2],
                "customer_email": r[3], "status": r[4],
                "total_amount": str(r[5]), "created_at": str(r[6]),
            } for r in rows],
            "total": total, "page": page, "limit": limit,
        }
    finally:
        conn.close()


def handle_order_get(args, user=None):
    order_id = args.get("order_id")
    if not order_id:
        return {"error": "order_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT o.id, o.order_number, o.customer_name, o.customer_email, o.customer_phone,
                      o.status, o.subtotal, o.tax_amount, o.discount_amount, o.total_amount,
                      o.notes, o.created_by, o.created_at, o.updated_at
               FROM orders o WHERE o.id = %s""",
            (order_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Order not found"}

        cur.execute(
            """SELECT oi.id, oi.product_id, p.name, p.sku, oi.quantity, oi.unit_price, oi.total_price
               FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s""",
            (order_id,),
        )
        items = [{
            "id": str(r[0]), "product_id": str(r[1]), "product_name": r[2],
            "sku": r[3], "quantity": r[4], "unit_price": str(r[5]), "total_price": str(r[6]),
        } for r in cur.fetchall()]

        return {
            "id": str(row[0]), "order_number": row[1],
            "customer_name": row[2], "customer_email": row[3], "customer_phone": row[4],
            "status": row[5], "subtotal": str(row[6]),
            "tax_amount": str(row[7]), "discount_amount": str(row[8]),
            "total_amount": str(row[9]), "notes": row[10],
            "created_by": str(row[11]), "created_at": str(row[12]),
            "updated_at": str(row[13]), "items": items,
        }
    finally:
        conn.close()


def _deduct_stock_for_order(cur, order_id, warehouse_id, user_id):
    cur.execute(
        "SELECT oi.product_id, oi.quantity, p.name FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s",
        (order_id,),
    )
    items = cur.fetchall()
    if not warehouse_id:
        cur.execute(
            "SELECT id FROM warehouses WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
        wh = cur.fetchone()
        if not wh:
            raise ValueError("No active warehouse found")
        warehouse_id = str(wh[0])

    for product_id, qty, product_name in items:
        cur.execute("SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s", (str(
            product_id), warehouse_id))
        stock_row = cur.fetchone()
        current_qty = stock_row[1] if stock_row else 0
        if current_qty < qty:
            raise ValueError(
                f"Insufficient stock for {product_name}: have {current_qty}, need {qty}")
        new_qty = current_qty - qty
        if stock_row:
            cur.execute(
                "UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)", (str(
                product_id), warehouse_id, new_qty))
        cur.execute(
            "INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by) VALUES (%s, %s, 'deduct', %s, %s, %s)",
            (str(product_id), warehouse_id, qty,
             f"Order confirmed (order_id: {order_id})", user_id),
        )


def _restore_stock_for_order(cur, order_id, user_id):
    cur.execute(
        "SELECT oi.product_id, oi.quantity FROM order_items oi WHERE oi.order_id = %s", (order_id,))
    items = cur.fetchall()
    cur.execute(
        "SELECT DISTINCT warehouse_id FROM stock_movements WHERE reason LIKE %s AND movement_type = 'deduct' AND warehouse_id IS NOT NULL LIMIT 1",
        (f"%{order_id}%",),
    )
    wh_row = cur.fetchone()
    if not wh_row:
        cur.execute(
            "SELECT id FROM warehouses WHERE is_active = TRUE ORDER BY created_at LIMIT 1")
        wh_row = cur.fetchone()
    warehouse_id = str(wh_row[0]) if wh_row else None
    if not warehouse_id:
        return

    for product_id, qty in items:
        cur.execute("SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s", (str(
            product_id), warehouse_id))
        stock_row = cur.fetchone()
        current_qty = stock_row[1] if stock_row else 0
        new_qty = current_qty + qty
        if stock_row:
            cur.execute(
                "UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)", (str(
                product_id), warehouse_id, new_qty))
        cur.execute(
            "INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by) VALUES (%s, %s, 'add', %s, %s, %s)",
            (str(product_id), warehouse_id, qty,
             f"Order cancelled - stock restored (order_id: {order_id})", user_id),
        )


def handle_order_update_status(args, user=None):
    order_id = args.get("order_id")
    new_status = (args.get("status") or "").strip().lower()
    warehouse_id = args.get("warehouse_id")

    if not order_id:
        return {"error": "order_id is required"}
    if not new_status:
        return {"error": "status is required"}
    if new_status == "cancelled":
        return {"error": "Use order_cancel to cancel orders"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, status, order_number FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if not order:
            return {"error": "Order not found"}

        current_status = order[1]
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            return {"error": f"Cannot transition from '{current_status}' to '{new_status}'. Allowed: {', '.join(allowed) if allowed else 'none'}"}

        if new_status == "confirmed":
            _deduct_stock_for_order(
                cur, order_id, warehouse_id, user["user_id"])

        cur.execute(
            "UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s", (new_status, order_id))
        cur.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, changed_by) VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (order_id, current_status, new_status, user["user_id"]),
        )
        history_row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "update_order_status", "orders", entity_id=order_id,
                   details={"order_number": order[2], "from": current_status, "to": new_status})

        result = {
            "order_id": str(order[0]), "order_number": order[2],
            "previous_status": current_status, "new_status": new_status,
            "changed_at": str(history_row[1]),
        }
        if new_status == "confirmed":
            result["stock_deducted"] = True
        return result
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_order_cancel(args, user=None):
    order_id = args.get("order_id")
    confirmed = args.get("confirm", False)

    if not order_id:
        return {"error": "order_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, status, order_number, customer_name, total_amount FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if not order:
            return {"error": "Order not found"}

        current_status = order[1]
        if current_status == "cancelled":
            return {"error": "Order is already cancelled"}
        if current_status == "delivered":
            return {"error": "Cannot cancel a delivered order. Use returns instead."}

        if not confirmed:
            msg = f"Are you sure you want to cancel Order {order[2]} ({order[3]}, total ₹{order[4]})?"
            if current_status == "confirmed":
                msg += " Stock that was deducted will be restored."
            return {
                "confirmation_required": True, "message": msg,
                "order_id": str(order[0]), "order_number": order[2],
                "current_status": current_status,
                "instruction": "Call again with confirm: true to proceed.",
            }

        if current_status == "confirmed":
            _restore_stock_for_order(cur, order_id, user["user_id"])

        cur.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = NOW() WHERE id = %s", (order_id,))
        cur.execute(
            "INSERT INTO order_status_history (order_id, from_status, to_status, changed_by) VALUES (%s, %s, 'cancelled', %s) RETURNING created_at",
            (order_id, current_status, user["user_id"]),
        )
        history_row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "cancel_order", "orders", entity_id=order_id,
                   details={"order_number": order[2], "from": current_status, "stock_restored": current_status == "confirmed"})

        result = {
            "order_id": str(order[0]), "order_number": order[2],
            "previous_status": current_status, "new_status": "cancelled",
            "changed_at": str(history_row[0]),
        }
        if current_status == "confirmed":
            result["stock_restored"] = True
        return result
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_order_history(args, user=None):
    order_id = args.get("order_id")
    if not order_id:
        return {"error": "order_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, order_number, status FROM orders WHERE id = %s", (order_id,))
        order = cur.fetchone()
        if not order:
            return {"error": "Order not found"}

        cur.execute(
            """SELECT h.id, h.from_status, h.to_status, h.changed_by, u.full_name, h.created_at
               FROM order_status_history h LEFT JOIN users u ON h.changed_by = u.id
               WHERE h.order_id = %s ORDER BY h.created_at ASC""",
            (order_id,),
        )
        rows = cur.fetchall()
        return {
            "order_id": str(order[0]), "order_number": order[1], "current_status": order[2],
            "history": [{
                "id": str(r[0]), "from_status": r[1], "to_status": r[2],
                "changed_by": str(r[3]) if r[3] else None, "changed_by_name": r[4],
                "created_at": str(r[5]),
            } for r in rows],
            "total_transitions": len(rows),
        }
    finally:
        conn.close()


# ── Invoice Handlers ───────────────────────────────────────────────────


def _get_org_settings(cur):
    """Load org_settings as a flat dict."""
    cur.execute("SELECT setting_key, setting_value FROM org_settings")
    return {r[0]: r[1] for r in cur.fetchall()}


def handle_invoice_generate(args, user=None):
    order_id = args.get("order_id")
    tax_rate = args.get("tax_rate", 0)
    due_date = args.get("due_date")
    notes = (args.get("notes") or "").strip() or None

    if not order_id:
        return {"error": "order_id is required"}
    try:
        tax_rate = float(tax_rate)
        if tax_rate < 0 or tax_rate > 100:
            raise ValueError
    except (ValueError, TypeError):
        return {"error": "tax_rate must be between 0 and 100"}

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Load org settings + per-invoice overrides
        settings = _get_org_settings(cur)
        for key in ("currency_symbol", "tax_label", "payment_terms", "invoice_footer"):
            if args.get(key):
                settings[key] = args[key]

        cur.execute(
            """SELECT id, order_number, customer_name, customer_email, customer_phone, status, subtotal
               FROM orders WHERE id = %s""",
            (order_id,),
        )
        order = cur.fetchone()
        if not order:
            return {"error": "Order not found"}

        order_data = {"order_number": order[1], "customer_name": order[2],
                      "customer_email": order[3], "customer_phone": order[4], "status": order[5]}

        cur.execute(
            "SELECT id, invoice_number FROM invoices WHERE order_id = %s", (order_id,))
        existing = cur.fetchone()
        if existing:
            return {"error": f"Invoice {existing[1]} already exists for this order"}

        cur.execute(
            """SELECT oi.product_id, p.name, p.sku, oi.quantity, oi.unit_price, oi.total_price
               FROM order_items oi JOIN products p ON oi.product_id = p.id WHERE oi.order_id = %s""",
            (order_id,),
        )
        items = [{"product_id": str(r[0]), "product_name": r[1], "sku": r[2], "quantity": r[3], "unit_price": float(
            r[4]), "total_price": float(r[5])} for r in cur.fetchall()]
        if not items:
            return {"error": "Order has no items"}

        subtotal = sum(item["total_price"] for item in items)
        tax_amount = round(subtotal * tax_rate / 100, 2)
        total_amount = round(subtotal + tax_amount, 2)

        invoice_number = _generate_number("INV")
        for _ in range(5):
            cur.execute(
                "SELECT id FROM invoices WHERE invoice_number = %s", (invoice_number,))
            if not cur.fetchone():
                break
            invoice_number = _generate_number("INV")

        if not due_date:
            due_date = (datetime.now(timezone.utc) +
                        timedelta(days=30)).strftime("%Y-%m-%d")

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        invoice_data = {"invoice_number": invoice_number, "subtotal": subtotal, "tax_rate": tax_rate,
                        "tax_amount": tax_amount, "total_amount": total_amount, "due_date": due_date,
                        "created_at": created_at, "notes": notes}

        # Build PDF invoice
        pdf_bytes = build_invoice_pdf(invoice_data, items, order_data, settings)

        s3_key = f"invoices/{invoice_number}.pdf"
        s3_bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(Bucket=s3_bucket, Key=s3_key, Body=pdf_bytes,
                      ContentType="application/pdf")

        cur.execute(
            """INSERT INTO invoices (invoice_number, order_id, pdf_s3_key, subtotal, tax_rate, tax_amount, total_amount, payment_status, status, due_date, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'unpaid', 'generated', %s, %s) RETURNING id, created_at""",
            (invoice_number, order_id, s3_key, subtotal, tax_rate,
             tax_amount, total_amount, due_date, user["user_id"]),
        )
        inv_row = cur.fetchone()
        conn.commit()

        log_action(user["user_id"], "generate_invoice", "invoices", entity_id=str(inv_row[0]),
                   details={"invoice_number": invoice_number, "order_number": order_data["order_number"], "total": str(total_amount)})

        return {
            "id": str(inv_row[0]), "invoice_number": invoice_number,
            "order_id": str(order_id), "order_number": order_data["order_number"],
            "customer_name": order_data["customer_name"],
            "subtotal": str(subtotal), "tax_rate": str(tax_rate),
            "tax_amount": str(tax_amount), "total_amount": str(total_amount),
            "currency": settings.get("currency_symbol", "₹"),
            "tax_label": settings.get("tax_label", "GST"),
            "payment_status": "unpaid", "status": "generated",
            "format": "pdf", "due_date": due_date, "created_at": str(inv_row[1]),
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


def handle_invoice_list(args, user=None):
    page = max(int(args.get("page", 1)), 1)
    limit = min(max(int(args.get("limit", 20)), 1), 100)
    offset = (page - 1) * limit
    payment_status = (args.get("payment_status") or "").strip().lower() or None
    from_date = args.get("from_date")
    to_date = args.get("to_date")
    search = (args.get("search") or "").strip()

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
        conditions.append(
            "(i.invoice_number ILIKE %s OR o.customer_name ILIKE %s OR o.order_number ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT COUNT(*) FROM invoices i JOIN orders o ON i.order_id = o.id {where}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"""SELECT i.id, i.invoice_number, o.order_number, o.customer_name,
                       i.total_amount, i.payment_status, i.status, i.due_date, i.created_at
                FROM invoices i JOIN orders o ON i.order_id = o.id {where}
                ORDER BY i.created_at DESC LIMIT %s OFFSET %s""",
            params + [limit, offset],
        )
        rows = cur.fetchall()
        return {
            "invoices": [{
                "id": str(r[0]), "invoice_number": r[1], "order_number": r[2],
                "customer_name": r[3], "total_amount": str(r[4]),
                "payment_status": r[5], "status": r[6],
                "due_date": str(r[7]) if r[7] else None, "created_at": str(r[8]),
            } for r in rows],
            "total": total, "page": page, "limit": limit,
        }
    finally:
        conn.close()


def handle_invoice_get(args, user=None):
    invoice_id = args.get("invoice_id")
    if not invoice_id:
        return {"error": "invoice_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT i.id, i.invoice_number, i.order_id, o.order_number, o.customer_name,
                      o.customer_email, i.subtotal, i.tax_rate, i.tax_amount, i.total_amount,
                      i.payment_status, i.status, i.due_date, i.sent_at, i.created_by, i.created_at
               FROM invoices i JOIN orders o ON i.order_id = o.id WHERE i.id = %s""",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invoice not found"}
        return {
            "id": str(row[0]), "invoice_number": row[1],
            "order_id": str(row[2]), "order_number": row[3],
            "customer_name": row[4], "customer_email": row[5],
            "subtotal": str(row[6]), "tax_rate": str(row[7]),
            "tax_amount": str(row[8]), "total_amount": str(row[9]),
            "payment_status": row[10], "status": row[11],
            "due_date": str(row[12]) if row[12] else None,
            "sent_at": str(row[13]) if row[13] else None,
            "created_by": str(row[14]), "created_at": str(row[15]),
        }
    finally:
        conn.close()


def handle_invoice_download(args, user=None):
    invoice_id = args.get("invoice_id")
    if not invoice_id:
        return {"error": "invoice_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, invoice_number, pdf_s3_key FROM invoices WHERE id = %s", (invoice_id,))
        row = cur.fetchone()
        if not row:
            return {"error": "Invoice not found"}
        if not row[2]:
            return {"error": "Invoice file not found. Regenerate the invoice."}

        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": row[2]}, ExpiresIn=900)
        return {"invoice_id": str(row[0]), "invoice_number": row[1], "download_url": url, "expires_in": "15 minutes"}
    finally:
        conn.close()


def handle_invoice_send(args, user=None):
    invoice_id = args.get("invoice_id")
    if not invoice_id:
        return {"error": "invoice_id is required"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT i.id, i.invoice_number, i.pdf_s3_key, o.customer_name, o.customer_email
               FROM invoices i JOIN orders o ON i.order_id = o.id WHERE i.id = %s""",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return {"error": "Invoice not found"}
        if not row[2]:
            return {"error": "Invoice file not found. Generate the invoice first."}

        s3 = boto3.client("s3", region_name="us-east-1")
        bucket = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": row[2]}, ExpiresIn=86400)

        now = datetime.now(timezone.utc)
        cur.execute(
            "UPDATE invoices SET sent_at = %s, status = 'sent' WHERE id = %s", (now, invoice_id))
        conn.commit()

        log_action(user["user_id"], "send_invoice", "invoices", entity_id=invoice_id,
                   details={"invoice_number": row[1], "customer_email": row[4]})

        result = {
            "invoice_id": str(row[0]), "invoice_number": row[1],
            "customer_name": row[3], "download_url": url,
            "status": "sent", "sent_at": str(now),
        }
        if row[4]:
            result["customer_email"] = row[4]
            result["email_note"] = "Email delivery available when SES is configured (Phase 4). Share the download link for now."
        else:
            result["email_note"] = "No customer email on file. Share the download link directly."
        return result
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


# ── Org Settings Handlers ─────────────────────────────────────────────


def handle_org_settings_get(args, user=None):
    """Return all org settings as a readable dict."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        settings = _get_org_settings(cur)
        return {
            "settings": settings,
            "available_keys": [
                "company_name", "company_address", "company_phone", "company_email",
                "company_logo_s3_key", "currency_code", "currency_symbol",
                "tax_label", "payment_terms", "invoice_footer", "locale",
            ],
            "supported_currencies": {
                "INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "AED": "AED", "SGD": "S$",
            },
            "supported_tax_labels": ["GST", "VAT", "Sales Tax", "Tax"],
            "supported_locales": ["en-IN", "en-US", "en-GB", "en-EU", "en-AE", "en-SG"],
        }
    finally:
        conn.close()


def handle_org_settings_update(args, user=None):
    """Update one or more org settings."""
    updates = args.get("settings", {})
    if not updates or not isinstance(updates, dict):
        return {"error": "Provide 'settings' as a key-value object"}

    valid_keys = {
        "company_name", "company_address", "company_phone", "company_email",
        "company_logo_s3_key", "currency_code", "currency_symbol",
        "tax_label", "payment_terms", "invoice_footer", "locale",
    }
    invalid = set(updates.keys()) - valid_keys
    if invalid:
        return {"error": f"Invalid setting keys: {', '.join(invalid)}. Valid: {', '.join(sorted(valid_keys))}"}

    conn = get_connection()
    try:
        cur = conn.cursor()
        updated = []
        for key, value in updates.items():
            cur.execute(
                """INSERT INTO org_settings (setting_key, setting_value, updated_at, updated_by)
                   VALUES (%s, %s, NOW(), %s)
                   ON CONFLICT (setting_key) DO UPDATE SET setting_value = %s, updated_at = NOW(), updated_by = %s""",
                (key, str(value), user["user_id"], str(value), user["user_id"]),
            )
            updated.append(key)
        conn.commit()

        log_action(user["user_id"], "update_org_settings", "org_settings",
                   details={"updated_keys": updated})

        # Return fresh settings
        settings = _get_org_settings(cur)
        return {"updated": updated, "settings": settings}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()


TOOL_HANDLERS = {
    "omnidesk_start": handle_omnidesk_start,
    "omnidesk_help": handle_omnidesk_help,
    "get_profile": handle_get_profile,
    "category_list": handle_category_list,
    "category_create": handle_category_create,
    "product_list": handle_product_list,
    "product_get": handle_product_get,
    "product_create": handle_product_create,
    "product_update": handle_product_update,
    "product_deactivate": handle_product_deactivate,
    "product_search": handle_product_search,
    "warehouse_list": handle_warehouse_list,
    "warehouse_create": handle_warehouse_create,
    "stock_check": handle_stock_check,
    "stock_adjust": handle_stock_adjust,
    "stock_low_alerts": handle_stock_low_alerts,
    "stock_movements": handle_stock_movements,
    "order_create": handle_order_create,
    "order_list": handle_order_list,
    "order_get": handle_order_get,
    "order_update_status": handle_order_update_status,
    "order_cancel": handle_order_cancel,
    "order_history": handle_order_history,
    "invoice_generate": handle_invoice_generate,
    "invoice_list": handle_invoice_list,
    "invoice_get": handle_invoice_get,
    "invoice_download": handle_invoice_download,
    "invoice_send": handle_invoice_send,
    "org_settings_get": handle_org_settings_get,
    "org_settings_update": handle_org_settings_update,
}

# ── JSON-RPC Helpers ────────────────────────────────────────────────────


def jsonrpc_response(req_id, result):
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}),
    }


def jsonrpc_error(req_id, code, message):
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": code, "message": message}}
        ),
    }


def extract_user_from_headers(event):
    """Extract JWT user from Authorization header.

    Handles multiple header formats:
      - mcp-remote: Authorization: Bearer <token>
      - Claude.ai Connectors: may lowercase headers or use different casing
      - Query param fallback: ?token=<jwt>
    """
    headers = event.get("headers") or {}

    # Try all common header casings
    auth_header = ""
    for key in ("Authorization", "authorization", "AUTHORIZATION"):
        if key in headers:
            auth_header = headers[key]
            break

    # If not found, search case-insensitively
    if not auth_header:
        for key, value in headers.items():
            if key.lower() == "authorization":
                auth_header = value
                break

    # Extract token from "Bearer <token>" format
    if auth_header.startswith("Bearer "):
        return verify_token(auth_header[7:], expected_type="access")
    if auth_header.startswith("bearer "):
        return verify_token(auth_header[7:], expected_type="access")

    # If auth header has a raw JWT (no Bearer prefix), try it directly
    if auth_header and auth_header.count(".") == 2:
        return verify_token(auth_header, expected_type="access")

    # Fallback: check query string params (useful for testing)
    qsp = event.get("queryStringParameters") or {}
    token = qsp.get("token")
    if token:
        return verify_token(token, expected_type="access")

    # Log headers for debugging (redact sensitive values)
    import logging
    logger = logging.getLogger()
    header_keys = list(headers.keys()) if headers else []
    logger.info(f"MCP auth failed. Header keys present: {header_keys}")

    return None


# ── Lambda Handler ──────────────────────────────────────────────────────


def lambda_handler(event, context):
    import logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    method = event.get("httpMethod") or event.get(
        "requestContext", {}).get("http", {}).get("method", "")
    headers = event.get("headers") or {}
    body_raw = event.get("body") or ""

    # Log every request for debugging connector issues
    logger.info(
        f"MCP REQUEST: method={method}, header_keys={list(headers.keys())}, body_preview={str(body_raw)[:200]}")

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    if method == "GET":
        return {"statusCode": 405, "headers": CORS_HEADERS, "body": "SSE not supported in Lambda mode"}

    if method == "DELETE":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # Parse JSON-RPC
    try:
        body = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return jsonrpc_error(None, -32700, "Parse error")

    req_id = body.get("id")
    rpc_method = body.get("method")
    params = body.get("params", {})

    # initialize
    if rpc_method == "initialize":
        return jsonrpc_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    # notifications/initialized
    if rpc_method == "notifications/initialized":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    # tools/list
    if rpc_method == "tools/list":
        return jsonrpc_response(req_id, {"tools": TOOLS})

    # tools/call
    if rpc_method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return jsonrpc_error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            # Extract user from Authorization header
            user = extract_user_from_headers(event)
            if not user:
                return jsonrpc_response(req_id, {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "Authentication required. Your token is missing or expired. Please generate a new token via /api/auth/login and update your Claude Desktop config."
                    })}],
                    "isError": True,
                })

            # Check RBAC
            if not check_role(user, tool_name):
                return jsonrpc_response(req_id, {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": f"Permission denied. Your role '{user.get('role')}' cannot access '{tool_name}'. Minimum role: {TOOL_ROLES.get(tool_name, 'viewer')}."
                    })}],
                    "isError": True,
                })

            result = handler(arguments, user=user)

            # If handler returns pre-formatted markdown, pass it directly
            if isinstance(result, dict) and "text" in result and len(result) == 1:
                display_text = result["text"]
            else:
                display_text = json.dumps(result, default=str)

            return jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": display_text}],
            })
        except Exception as e:
            return jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                "isError": True,
            })

    # ping
    if rpc_method == "ping":
        return jsonrpc_response(req_id, {})

    return jsonrpc_error(req_id, -32601, f"Method not found: {rpc_method}")
