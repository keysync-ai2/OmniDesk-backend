"""Lambda: omnidesk-form-submit
POST /api/forms/{id}/submit — Public endpoint (no auth) for form submissions.
Validates against form schema, stores data in DynamoDB, indexes in Pinecone.
"""
import base64
import json
import os
import uuid
from datetime import datetime
import boto3
from utils.db import get_connection
from utils.response import success, error

S3_BUCKET = os.environ.get("S3_BUCKET", "omnidesk-files-577397739686")
DYNAMO_TABLE = os.environ.get("FORM_SUBMISSIONS_TABLE", "omnidesk-form-submissions")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

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


def _is_file_object(value):
    """Check if a value is a base64-encoded file object from the client."""
    return (
        isinstance(value, dict)
        and "data" in value
        and "name" in value
        and isinstance(value.get("data"), str)
    )


def _upload_file_to_s3(form_id, submission_id, field_name, file_obj):
    """Upload a base64-encoded file to S3. Returns S3 key or None."""
    file_data = base64.b64decode(file_obj["data"])
    if len(file_data) > MAX_FILE_SIZE:
        return None, f"{field_name}: file exceeds 10 MB limit"

    original_name = file_obj.get("name", "attachment")
    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in original_name)
    content_type = file_obj.get("type", "application/octet-stream")

    s3_key = f"forms/{form_id}/submissions/{submission_id}/{field_name}/{safe_name}"
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key,
        Body=file_data,
        ContentType=content_type,
    )
    return s3_key, None


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


CLOSED_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{form_name} — OmniDesk Form</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #f7f5f2; color: #2d2a26; line-height: 1.6;
    min-height: 100vh; display: flex; justify-content: center; align-items: center;
    padding: 40px 20px;
  }}
  .closed-card {{
    background: white; border-radius: 12px; padding: 48px 40px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center;
    max-width: 480px; width: 100%;
  }}
  .closed-card .icon {{ font-size: 3em; margin-bottom: 16px; color: #5a8a5e; }}
  .closed-card h2 {{ font-size: 1.4em; margin-bottom: 8px; color: #2d2a26; }}
  .closed-card p {{ color: #6b6560; font-size: 0.95em; }}
  .footer {{ text-align: center; padding: 16px; color: #999; font-size: 0.8em; margin-top: 20px; }}
</style>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" crossorigin="anonymous" referrerpolicy="no-referrer" />
</head>
<body>
<div>
  <div class="closed-card">
    <div class="icon"><i class="fa-solid fa-circle-check"></i></div>
    <h2>Form Closed</h2>
    <p>This form has already been submitted and is no longer accepting responses.</p>
  </div>
  <div class="footer">Powered by OmniDesk</div>
</div>
</body>
</html>"""


CF_DISTRIBUTION_ID = "EKUK67FUENEPR"
cf_client = boto3.client("cloudfront", region_name="us-east-1")


def _replace_form_with_closed_page(form_id, form_name):
    """Replace the live form HTML on S3 with a 'Form Closed' page and invalidate CloudFront cache."""
    try:
        s3_key = f"forms/{form_id}/form.html"
        html = CLOSED_PAGE_HTML.format(form_name=form_name)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=s3_key,
            Body=html.encode("utf-8"),
            ContentType="text/html",
        )
        # Invalidate CloudFront cache so the closed page is served immediately
        cf_client.create_invalidation(
            DistributionId=CF_DISTRIBUTION_ID,
            InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": [f"/{s3_key}"]},
                "CallerReference": f"{form_id}-{uuid.uuid4().hex[:8]}",
            },
        )
    except Exception as e:
        print(f"[form-submit] Failed to replace form with closed page: {e}")


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

    # Separate file fields from regular data before validation
    submission_data = {}
    file_fields = {}
    for key, value in body.items():
        if _is_file_object(value):
            file_fields[key] = value
            # Store filename in submission_data so validation sees a value
            submission_data[key] = value.get("name", "uploaded_file")
        else:
            submission_data[key] = value

    # Validate against schema (uses submission_data which has filenames for file fields)
    validation_errors = _validate_submission(submission_data, fields)
    if validation_errors:
        return error(f"Validation failed: {'; '.join(validation_errors)}", 400)

    # Generate submission ID and timestamp
    submission_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    sort_key = f"{timestamp}#{submission_id}"

    # Upload files to S3
    s3_artifacts = []
    for field_name, file_obj in file_fields.items():
        try:
            s3_key, upload_err = _upload_file_to_s3(form_id, submission_id, field_name, file_obj)
            if upload_err:
                return error(upload_err, 400)
            s3_artifacts.append(s3_key)
            # Replace file data with metadata in stored submission
            submission_data[field_name] = {
                "filename": file_obj.get("name"),
                "content_type": file_obj.get("type"),
                "size": file_obj.get("size"),
                "s3_key": s3_key,
            }
        except Exception as e:
            return error(f"File upload failed for {field_name}: {str(e)}", 500)

    # Store in DynamoDB
    dynamo_item = {
        "form_id": str(form_id),
        "submission_id": sort_key,
        "data": submission_data,
        "submitted_at": timestamp,
        "submission_uuid": submission_id,
    }
    if s3_artifacts:
        dynamo_item["s3_artifacts"] = s3_artifacts

    # Check for any metadata from headers (optional)
    headers = event.get("headers") or {}
    user_agent = headers.get("User-Agent") or headers.get("user-agent")
    source_ip = (event.get("requestContext") or {}).get("identity", {}).get("sourceIp")
    if user_agent:
        dynamo_item["user_agent"] = user_agent
    if source_ip:
        dynamo_item["source_ip"] = source_ip

    table.put_item(Item=dynamo_item)

    # Update submission count and deactivate form in PostgreSQL
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """UPDATE forms
               SET submission_count = COALESCE(submission_count, 0) + 1,
                   is_active = FALSE
               WHERE id = %s""",
            (form_id,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()

    # Replace S3 form HTML with a "Form Closed" page
    _replace_form_with_closed_page(form_id, form_name)

    # Index in Pinecone for semantic search
    submission_text = _build_submission_text(form_name, submission_data, fields)
    _index_pinecone(form_id, submission_id, submission_text)

    result = {
        "message": "Submission received successfully",
        "submission_id": submission_id,
        "form_id": str(form_id),
        "submitted_at": timestamp,
    }
    if s3_artifacts:
        result["files_uploaded"] = len(s3_artifacts)

    return success(result, 201)


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)
    if method == "POST":
        return _handler(event, context)
    return error("Method not allowed", 405)
