"""Audit logging — writes to DynamoDB omnidesk-audit-log table."""
import os
import uuid
from datetime import datetime, timezone
import boto3

_table = None


def _get_table():
    global _table
    if _table:
        return _table
    table_name = os.environ.get("AUDIT_TABLE", "omnidesk-audit-log")
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    _table = dynamodb.Table(table_name)
    return _table


def log_action(user_id, action, module, entity_id=None, details=None, ip_address=None):
    """Write an audit log entry to DynamoDB.

    Args:
        user_id: UUID of the user performing the action
        action: e.g., 'register', 'login', 'create_order'
        module: e.g., 'auth', 'orders', 'products'
        entity_id: optional ID of the affected entity
        details: optional dict with extra context
        ip_address: optional client IP
    """
    now = datetime.now(timezone.utc).isoformat()
    action_id = str(uuid.uuid4())

    item = {
        "user_id": str(user_id),
        "timestamp_action_id": f"{now}#{action_id}",
        "action": action,
        "module": module,
        "timestamp": now,
    }

    if entity_id:
        item["entity_id"] = str(entity_id)
    if details:
        item["details"] = details
    if ip_address:
        item["ip_address"] = ip_address

    try:
        _get_table().put_item(Item=item)
    except Exception:
        # Audit logging should never break the main flow
        pass
