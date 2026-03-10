"""Lambda: omnidesk-form-submissions
GET /api/forms/{id}/submissions       — List all submissions for a form (paginated)
GET /api/forms/{id}/submissions/{sid} — Get single submission details
"""
import json
import os
import boto3
from boto3.dynamodb.conditions import Key
from utils.db import get_connection
from utils.response import success, error
from utils.auth_middleware import require_auth
from utils.cloudfront_signer import generate_signed_url

DYNAMO_TABLE = os.environ.get("FORM_SUBMISSIONS_TABLE", "omnidesk-form-submissions")

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table(DYNAMO_TABLE)


def _get_form(form_id):
    """Verify form exists and is active."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, is_active FROM forms WHERE id = %s",
            (form_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _list_submissions(event, context):
    """List all submissions for a form."""
    path_params = event.get("pathParameters") or {}
    form_id = path_params.get("id")

    if not form_id:
        return error("Form ID is required", 400)

    form = _get_form(form_id)
    if not form:
        return error("Form not found", 404)

    qs = event.get("queryStringParameters") or {}
    limit = min(max(int(qs.get("limit", 50)), 1), 100)

    # Query DynamoDB — submissions sorted by timestamp (sort key)
    query_params = {
        "KeyConditionExpression": Key("form_id").eq(str(form_id)),
        "ScanIndexForward": False,  # newest first
        "Limit": limit,
    }

    # Pagination via last_key
    last_key = qs.get("last_key")
    if last_key:
        query_params["ExclusiveStartKey"] = {
            "form_id": str(form_id),
            "submission_id": last_key,
        }

    response = table.query(**query_params)
    items = response.get("Items", [])

    submissions = []
    for item in items:
        submissions.append({
            "submission_id": item.get("submission_uuid"),
            "sort_key": item.get("submission_id"),
            "data": item.get("data", {}),
            "submitted_at": item.get("submitted_at"),
            "source_ip": item.get("source_ip"),
        })

    result = {
        "form_id": str(form_id),
        "form_name": form[1],
        "submissions": submissions,
        "count": len(submissions),
    }

    # Include pagination token if more results exist
    if response.get("LastEvaluatedKey"):
        result["next_key"] = response["LastEvaluatedKey"].get("submission_id")

    return success(result)


def _get_single_submission(event, context):
    """Get a single submission by form_id and submission_uuid."""
    path_params = event.get("pathParameters") or {}
    form_id = path_params.get("id")
    sub_id = path_params.get("sub_id")

    if not form_id or not sub_id:
        return error("Form ID and submission ID are required", 400)

    form = _get_form(form_id)
    if not form:
        return error("Form not found", 404)

    # Query by form_id and filter for the specific submission_uuid
    response = table.query(
        KeyConditionExpression=Key("form_id").eq(str(form_id)),
        FilterExpression="submission_uuid = :sid",
        ExpressionAttributeValues={":sid": sub_id},
    )
    items = response.get("Items", [])

    if not items:
        return error("Submission not found", 404)

    item = items[0]

    # Generate CloudFront signed URLs for any S3 artifacts
    artifacts = item.get("s3_artifacts") or []
    artifact_urls = []
    for s3_key in artifacts:
        url = generate_signed_url(s3_key, expires_in=300)
        artifact_urls.append({"s3_key": s3_key, "url": url})

    return success({
        "form_id": str(form_id),
        "form_name": form[1],
        "submission_id": item.get("submission_uuid"),
        "data": item.get("data", {}),
        "submitted_at": item.get("submitted_at"),
        "source_ip": item.get("source_ip"),
        "artifacts": artifact_urls,
    })


list_handler = require_auth(_list_submissions, min_role="viewer")
get_handler = require_auth(_get_single_submission, min_role="viewer")


def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return success({}, 204)

    path_params = event.get("pathParameters") or {}
    sub_id = path_params.get("sub_id")

    if method == "GET" and sub_id:
        return get_handler(event, context)
    if method == "GET":
        return list_handler(event, context)

    return error("Method not allowed", 405)
