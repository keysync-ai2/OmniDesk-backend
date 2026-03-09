"""Lambda: omnidesk-product-search
GET /api/products/search?q=<natural language query>
Semantic search via Pinecone integrated inference + PostgreSQL product details.
"""
import json
import os
import boto3
from pinecone import Pinecone
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth

_pc_client = None
_pc_index = None

PINECONE_INDEX_NAME = "omnidesk-products"


def _get_pinecone_index():
    """Get Pinecone index (cached per Lambda container)."""
    global _pc_client, _pc_index
    if _pc_index:
        return _pc_index

    secret_name = os.environ.get("PINECONE_SECRET_ARN", "omnidesk/pinecone")
    client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = json.loads(
        client.get_secret_value(SecretId=secret_name)["SecretString"]
    )
    api_key = secret["api_key"]

    _pc_client = Pinecone(api_key=api_key)
    _pc_index = _pc_client.Index(PINECONE_INDEX_NAME)
    return _pc_index


def _handler(event, context):
    qs = event.get("queryStringParameters") or {}
    query = (qs.get("q") or "").strip()
    if not query:
        return error("Query parameter 'q' is required", 400)

    top_k = min(max(int(qs.get("top_k", 10)), 1), 50)

    try:
        index = _get_pinecone_index()

        # Search using integrated inference — Pinecone embeds the query text automatically
        results = index.search(
            namespace="products",
            query={
                "inputs": {"text": query},
                "top_k": top_k,
            },
            fields=["product_text"],
        )

        # Extract product IDs and scores from results
        # Response is a Pinecone SDK object — use attribute access
        hits = results.result.hits if hasattr(results, 'result') else []
        if not hits:
            return success({"products": [], "total": 0, "query": query})

        product_ids = []
        score_map = {}
        for hit in hits:
            pid = hit["_id"]
            product_ids.append(pid)
            score_map[pid] = hit.get("_score", 0)

        # Sort by score descending (Pinecone may not guarantee order)
        product_ids.sort(key=lambda pid: score_map.get(pid, 0), reverse=True)

        # Fetch full product details from PostgreSQL
        conn = get_connection()
        try:
            cur = conn.cursor()
            placeholders = ", ".join(["%s"] * len(product_ids))
            cur.execute(
                f"""
                SELECT p.id, p.sku, p.name, p.description, p.category_id, c.name as category_name,
                       p.unit_price, p.unit, p.created_at
                FROM products p
                LEFT JOIN categories c ON p.category_id = c.id
                WHERE p.id::text IN ({placeholders}) AND p.is_active = TRUE
                """,
                product_ids,
            )
            rows = cur.fetchall()

            # Build response ordered by Pinecone score
            products_by_id = {}
            for r in rows:
                pid = str(r[0])
                products_by_id[pid] = {
                    "id": pid,
                    "sku": r[1],
                    "name": r[2],
                    "description": r[3],
                    "category_id": str(r[4]) if r[4] else None,
                    "category_name": r[5],
                    "unit_price": str(r[6]),
                    "unit": r[7],
                    "created_at": str(r[8]),
                    "relevance_score": round(score_map.get(pid, 0), 4),
                }

            # Return in order of relevance (Pinecone ranking)
            ordered_products = [
                products_by_id[pid] for pid in product_ids if pid in products_by_id
            ]

            return success({
                "products": ordered_products,
                "total": len(ordered_products),
                "query": query,
            })
        finally:
            conn.close()

    except Exception as e:
        return error(f"Search failed: {str(e)}", 500)


handler = require_auth(_handler, min_role="viewer")


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return success({}, 204)
    return handler(event, context)
