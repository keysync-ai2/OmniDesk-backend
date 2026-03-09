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
import json
import os
import boto3
from pinecone import Pinecone
from utils.db import get_connection
from utils.jwt_helper import verify_token
from utils.audit import log_action

SERVER_INFO = {
    "name": "omnidesk-mcp",
    "version": "2.1.0",
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
        "description": "Login to OmniDesk. Call this FIRST when the user says 'Login OmniDesk', 'Start OmniDesk', 'Open OmniDesk', or greets you. Returns: user profile, low stock alerts, recent activity summary, and a welcome message with available actions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "omnidesk_help",
        "description": "Show the OmniDesk help menu. Call this when the user asks 'help', 'what can you do', 'show commands', or 'how to use OmniDesk'. Returns a categorized list of all available tools with usage examples.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {"type": "string", "enum": ["all", "products", "stock", "categories", "warehouses", "auth"], "description": "Filter help by module (default: all)"},
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

        # Low stock alerts
        cur.execute(
            """SELECT p.name, p.sku, s.quantity, s.low_stock_threshold, w.name
               FROM stock s JOIN products p ON s.product_id = p.id JOIN warehouses w ON s.warehouse_id = w.id
               WHERE s.quantity < s.low_stock_threshold AND p.is_active = TRUE
               ORDER BY (s.low_stock_threshold - s.quantity) DESC LIMIT 5"""
        )
        low_stock = [
            {"product": r[0], "sku": r[1], "quantity": r[2], "threshold": r[3], "warehouse": r[4]}
            for r in cur.fetchall()
        ]

        return {
            "welcome": f"Welcome back, {profile['full_name']}!",
            "profile": profile,
            "dashboard": {
                "total_products": total_products,
                "total_categories": total_categories,
                "total_warehouses": total_warehouses,
                "low_stock_alerts": len(low_stock),
            },
            "low_stock_items": low_stock if low_stock else "All products are well stocked.",
            "quick_actions": [
                "Try: 'Show me all products'",
                "Try: 'Add a new product'",
                "Try: 'Check stock for [product name]'",
                "Try: 'Search for [description]'",
                "Try: 'Show low stock alerts'",
                "Say 'help' for full command list",
            ],
        }
    finally:
        conn.close()


def handle_omnidesk_help(args, user=None):
    """Return categorized help menu with usage examples."""
    module_filter = (args.get("module") or "all").lower()

    help_sections = {
        "auth": {
            "title": "Auth & Profile",
            "tools": [
                {"command": "omnidesk_start", "description": "Login dashboard — your profile, stock alerts, quick stats", "example": "Login OmniDesk"},
                {"command": "get_profile", "description": "View your profile details", "example": "Show my profile"},
            ],
        },
        "categories": {
            "title": "Categories",
            "tools": [
                {"command": "category_list", "description": "List all product categories", "example": "Show me all categories"},
                {"command": "category_create", "description": "Create a new category (manager+)", "example": "Create category 'Electronics'"},
            ],
        },
        "products": {
            "title": "Products",
            "tools": [
                {"command": "product_list", "description": "Browse products with filters", "example": "Show all products in Clothing"},
                {"command": "product_get", "description": "Get full details of a product", "example": "Show details of Blue T-Shirt"},
                {"command": "product_create", "description": "Add a new product (manager+)", "example": "Add product Red Polo, SKU RP001, ₹699"},
                {"command": "product_update", "description": "Update product fields (manager+)", "example": "Change Blue T-Shirt price to ₹549"},
                {"command": "product_deactivate", "description": "Remove a product (admin only)", "example": "Deactivate product Wireless Mouse"},
                {"command": "product_search", "description": "Smart search by description", "example": "Find comfortable cotton clothing"},
            ],
        },
        "warehouses": {
            "title": "Warehouses",
            "tools": [
                {"command": "warehouse_list", "description": "List all warehouses", "example": "Show me all warehouses"},
                {"command": "warehouse_create", "description": "Add a new warehouse (admin only)", "example": "Create warehouse 'Mumbai Hub'"},
            ],
        },
        "stock": {
            "title": "Stock Management",
            "tools": [
                {"command": "stock_check", "description": "Check stock level for a product", "example": "How many Blue T-Shirts in stock?"},
                {"command": "stock_adjust", "description": "Add/deduct/set stock (staff+)", "example": "Add 50 Blue T-Shirts to Main Warehouse"},
                {"command": "stock_low_alerts", "description": "Products running low on stock", "example": "What needs restocking?"},
                {"command": "stock_movements", "description": "Stock change history (manager+)", "example": "Show stock history for Blue T-Shirt"},
            ],
        },
    }

    if module_filter != "all" and module_filter in help_sections:
        sections = {module_filter: help_sections[module_filter]}
    else:
        sections = help_sections

    role_info = {
        "viewer": "Can view products, stock, categories",
        "staff": "Can adjust stock + all viewer actions",
        "manager": "Can create/update products & categories + all staff actions",
        "admin": "Full access — create warehouses, deactivate products, all actions",
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
        cur.execute("SELECT id, name, description, created_at FROM categories WHERE is_active = TRUE ORDER BY name")
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
        cur.execute("SELECT id FROM categories WHERE name = %s AND is_active = TRUE", (name,))
        if cur.fetchone():
            return {"error": f"Category '{name}' already exists"}
        cur.execute(
            "INSERT INTO categories (name, description) VALUES (%s, %s) RETURNING id, name, description, created_at",
            (name, description),
        )
        row = cur.fetchone()
        conn.commit()
        log_action(user["user_id"], "create_category", "categories", entity_id=str(row[0]), details={"name": row[1]})
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
                      p.unit_price, p.unit, p.is_active, p.created_by, p.created_at, p.updated_at
               FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.id = %s""",
            (product_id,),
        )
        r = cur.fetchone()
        if not r:
            return {"error": "Product not found"}
        if not r[8]:
            return {"error": "Product has been deactivated"}
        return {
            "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "category_name": r[5],
            "unit_price": str(r[6]), "unit": r[7], "created_by": str(r[9]),
            "created_at": str(r[10]), "updated_at": str(r[11]),
        }
    finally:
        conn.close()


def handle_product_create(args, user=None):
    sku = (args.get("sku") or "").strip().upper()
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip() or None
    category_id = args.get("category_id")
    unit_price = args.get("unit_price")
    unit = (args.get("unit") or "pcs").strip().lower()

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
            cur.execute("SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (category_id,))
            if not cur.fetchone():
                return {"error": "Category not found"}
        cur.execute("SELECT id FROM products WHERE sku = %s", (sku,))
        if cur.fetchone():
            return {"error": f"Product with SKU '{sku}' already exists"}

        cur.execute(
            """INSERT INTO products (sku, name, description, category_id, unit_price, unit, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id, sku, name, description, category_id, unit_price, unit, created_at""",
            (sku, name, description, category_id, unit_price, unit, user["user_id"]),
        )
        r = cur.fetchone()
        conn.commit()
        log_action(user["user_id"], "create_product", "products", entity_id=str(r[0]),
                   details={"sku": r[1], "name": r[2], "unit_price": str(r[5])})
        return {
            "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "unit_price": str(r[5]),
            "unit": r[6], "created_at": str(r[7]),
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

    allowed = {"name": str, "description": str, "category_id": str, "unit_price": float, "unit": str}
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

    if not updates:
        return {"error": "No fields to update"}

    updates.append("updated_at = NOW()")
    params.append(product_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_active FROM products WHERE id = %s", (product_id,))
        row = cur.fetchone()
        if not row:
            return {"error": "Product not found"}
        if not row[0]:
            return {"error": "Product has been deactivated"}

        if "category_id" in args and args["category_id"]:
            cur.execute("SELECT id FROM categories WHERE id = %s AND is_active = TRUE", (args["category_id"],))
            if not cur.fetchone():
                return {"error": "Category not found"}

        set_clause = ", ".join(updates)
        cur.execute(
            f"""UPDATE products SET {set_clause} WHERE id = %s AND is_active = TRUE
                RETURNING id, sku, name, description, category_id, unit_price, unit, updated_at""",
            params,
        )
        r = cur.fetchone()
        if not r:
            return {"error": "Product not found or deactivated"}
        conn.commit()
        log_action(user["user_id"], "update_product", "products", entity_id=product_id,
                   details={k: str(args[k]) for k in args if k in allowed})
        return {
            "id": str(r[0]), "sku": r[1], "name": r[2], "description": r[3],
            "category_id": str(r[4]) if r[4] else None, "unit_price": str(r[5]),
            "unit": r[6], "updated_at": str(r[7]),
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
        log_action(user["user_id"], "deactivate_product", "products", entity_id=product_id, details={"name": row[1]})
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
        cur.execute("SELECT id, name, address, created_at FROM warehouses WHERE is_active = TRUE ORDER BY name")
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
        log_action(user["user_id"], "create_warehouse", "warehouses", entity_id=str(row[0]), details={"name": row[1]})
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
        cur.execute("SELECT id, name, sku FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
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
                {"warehouse_id": str(r[0]), "warehouse_name": r[1], "quantity": r[2], "low_stock_threshold": r[3]}
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
        cur.execute("SELECT id, name FROM products WHERE id = %s AND is_active = TRUE", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": "Product not found"}

        cur.execute("SELECT id, name FROM warehouses WHERE id = %s AND is_active = TRUE", (warehouse_id,))
        warehouse = cur.fetchone()
        if not warehouse:
            return {"error": "Warehouse not found"}

        cur.execute("SELECT id, quantity FROM stock WHERE product_id = %s AND warehouse_id = %s", (product_id, warehouse_id))
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
            cur.execute("UPDATE stock SET quantity = %s, updated_at = NOW() WHERE id = %s", (new_qty, stock_row[0]))
        else:
            cur.execute("INSERT INTO stock (product_id, warehouse_id, quantity) VALUES (%s, %s, %s)", (product_id, warehouse_id, new_qty))

        cur.execute(
            """INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reason, performed_by)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at""",
            (product_id, warehouse_id, movement_type, quantity, reason, user["user_id"]),
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
        cur.execute("SELECT id, name, sku FROM products WHERE id = %s", (product_id,))
        product = cur.fetchone()
        if not product:
            return {"error": "Product not found"}

        conditions = ["sm.product_id = %s"]
        params = [product_id]
        if warehouse_id:
            conditions.append("sm.warehouse_id = %s")
            params.append(warehouse_id)
        where = " AND ".join(conditions)

        cur.execute(f"SELECT COUNT(*) FROM stock_movements sm WHERE {where}", params)
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
    secret = json.loads(sm.get_secret_value(SecretId=secret_name)["SecretString"])
    _pc_client = Pinecone(api_key=secret["api_key"])
    _pc_index = _pc_client.Index("omnidesk-products")
    return _pc_index


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
        product_ids = sorted([h["_id"] for h in hits], key=lambda pid: score_map.get(pid, 0), reverse=True)

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
            ordered = [products_by_id[pid] for pid in product_ids if pid in products_by_id]
            return {"products": ordered, "total": len(ordered), "query": query}
        finally:
            conn.close()
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}


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
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
        ),
    }


def extract_user_from_headers(event):
    """Extract JWT user from Authorization header set by mcp-remote."""
    headers = event.get("headers") or {}
    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        return verify_token(auth_header[7:], expected_type="access")
    return None


# ── Lambda Handler ──────────────────────────────────────────────────────


def lambda_handler(event, context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "")

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

            return jsonrpc_response(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, default=str)}],
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
