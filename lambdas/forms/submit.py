"""Lambda: omnidesk-form-submit
POST /api/forms/{id}/submit — Public endpoint (no auth) for form submissions.
Validates against form schema, stores data in DynamoDB, indexes in Pinecone.
"""
import json
import os
import uuid
from datetime import datetime
import boto3
from utils.db import get_connection
from utils.response import success, error

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
DYNAMO_TABLE = os.environ.get("FORM_SUBMISSIONS_TABLE", "omnidesk-form-submissions")

s3 = boto3.client("s3", region_name="us-east-1")
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table(DYNAMO_TABLE)


def _get_form_schema(form_id):
    """Fetch form schema from PostgreSQL."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, schema_json, is_active FROM forms WHERE id = %s",
            (form_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _validate_submission(data, fields):
    """Validate submission data against form field schema."""
    errors = []
    for field in fields:
        name = field["name"]
        required = field.get("required", False)
        field_type = field.get("type", "text")
        value = data.get(name)

        if required and (value is None or (isinstance(value, str) and not value.strip())):
            label = field.get("label", name.replace("_", " ").title())
            errors.append(f"{label} is required")

        if value and field_type == "email" and isinstance(value, str):
            if "@" not in value or "." not in value.split("@")[-1]:
                errors.append(f"Invalid email for {name}")

        if value and field_type == "number" and isinstance(value, str):
            try:
                float(value)
            except ValueError:
                errors.append(f"{name} must be a number")

    return errors


def _build_submission_text(form_name, data, fields):
    """Build text string for Pinecone indexing."""
    parts = [f"Form: {form_name}"]
    for field in fields:
        name = field["name"]
        value = data.get(name)
        if value:
            label = field.get("label", name.replace("_", " ").title())
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            parts.append(f"{label}: {value}")
    return " | ".join(parts)


def _index_pinecone(form_id, submission_id, text):
    """Index submission text in Pinecone for semantic search."""
    try:
        from utils.pinecone_helper import _get_index
        index = _get_index()
        index.upsert_records(
            namespace="form_submissions",
            records=[{
                "_id": f"{form_id}#{submission_id}",
                "submission_text": text,
            }],
        )
    except Exception as e:
        print(f"[form-submit] Pinecone index failed: {e}")


def _handler(event, context):
    path_params = event.get("pathParameters") or {}
    form_id = path_params.get("id")

    if not form_id:
        return error("Form ID is required", 400)

    # Get form schema
    form_row = _get_form_schema(form_id)
    if not form_row:
        return error("Form not found", 404)
    if not form_row[3]:  # is_active
        return error("This form is no longer accepting submissions", 404)

    form_name = form_row[1]
    fields = form_row[2] if isinstance(form_row[2], list) else json.loads(form_row[2] or "[]")

    # Parse submission body
    body = json.loads(event.get("body") or "{}")
    if not body:
        return error("Submission data is required", 400)

    # Validate against schema
    validation_errors = _validate_submission(body, fields)
    if validation_errors:
        return error(f"Validation failed: {'; '.join(validation_errors)}", 400)

    # Generate submission ID and timestamp
    submission_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    sort_key = f"{timestamp}#{submission_id}"

    # Store in DynamoDB
    dynamo_item = {
        "form_id": str(form_id),
        "submission_id": sort_key,
        "data": body,
        "submitted_at": timestamp,
        "submission_uuid": submission_id,
    }

    # Check for any metadata from headers (optional)
    headers = event.get("headers") or {}
    user_agent = headers.get("User-Agent") or headers.get("user-agent")
    source_ip = (event.get("requestContext") or {}).get("identity", {}).get("sourceIp")
    if user_agent:
        dynamo_item["user_agent"] = user_agent
    if source_ip:
        dynamo_item["source_ip"] = source_ip

    table.put_item(Item=dynamo_item)

    # Update submission count in PostgreSQL
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE forms SET submission_count = COALESCE(submission_count, 0) + 1
               WHERE id = %s""",
            (form_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    # Index in Pinecone for semantic search
    submission_text = _build_submission_text(form_name, body, fields)
    _index_pinecone(form_id, submission_id, submission_text)

    return success({
        "message": "Submission received successfully",
        "submission_id": submission_id,
        "form_id": str(form_id),
        "submitted_at": timestamp,
    }, 201)


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)
    if method == "POST":
        return _handler(event, context)
    return error("Method not allowed", 405)
