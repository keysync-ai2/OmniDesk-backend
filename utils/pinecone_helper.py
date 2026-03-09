"""Pinecone helper — upsert/delete product vectors using integrated inference."""
import json
import os
import boto3
from pinecone import Pinecone

_pc_client = None
_pc_index = None

PINECONE_INDEX_NAME = "omnidesk-products"
NAMESPACE = "products"


def _get_index():
    """Get Pinecone index (cached per Lambda container)."""
    global _pc_client, _pc_index
    if _pc_index:
        return _pc_index

    secret_name = os.environ.get("PINECONE_SECRET_ARN", "omnidesk/pinecone")
    client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = json.loads(
        client.get_secret_value(SecretId=secret_name)["SecretString"]
    )
    _pc_client = Pinecone(api_key=secret["api_key"])
    _pc_index = _pc_client.Index(PINECONE_INDEX_NAME)
    return _pc_index


def build_product_text(name, description=None, sku=None, unit=None, unit_price=None, extra_fields=None):
    """Build a combined text string for embedding a product."""
    parts = [name]
    if description:
        parts.append(description)
    if sku:
        parts.append(f"SKU: {sku}")
    if unit_price is not None:
        parts.append(f"Price: {unit_price} per {unit or 'pcs'}")
    if extra_fields and isinstance(extra_fields, dict):
        for key, val in extra_fields.items():
            if val:
                parts.append(f"{key}: {val}")
    return " | ".join(parts)


def upsert_product(product_id, name, description=None, sku=None, unit=None, unit_price=None, extra_fields=None):
    """Upsert a product vector into Pinecone using integrated inference.

    With integrated inference, we provide text and Pinecone handles embedding.
    """
    try:
        index = _get_index()
        product_text = build_product_text(name, description, sku, unit, unit_price, extra_fields)

        # Upsert record — Pinecone auto-embeds the product_text field
        index.upsert_records(
            namespace=NAMESPACE,
            records=[
                {
                    "_id": str(product_id),
                    "product_text": product_text,
                }
            ],
        )
        return True
    except Exception as e:
        # Log but don't fail the main operation
        print(f"[pinecone_helper] Failed to upsert product {product_id}: {e}")
        return False


def delete_product(product_id):
    """Delete a product vector from Pinecone."""
    try:
        index = _get_index()
        index.delete(ids=[str(product_id)], namespace=NAMESPACE)
        return True
    except Exception as e:
        print(f"[pinecone_helper] Failed to delete product {product_id}: {e}")
        return False
