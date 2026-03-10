"""CloudFront signed URL generator for secure file delivery."""
import json
import boto3
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import base64

# Cache across Lambda invocations
_private_key = None
_cf_domain = None

CF_KEY_PAIR_ID = "K2AUHDWUTRQ06"
CF_DISTRIBUTION_DOMAIN = "d299qc5zbgpiuq.cloudfront.net"
CF_SECRET_ARN = "omnidesk/cloudfront-key"


def _get_private_key():
    """Load CloudFront signing private key from Secrets Manager (cached)."""
    global _private_key
    if _private_key is not None:
        return _private_key

    client = boto3.client("secretsmanager", region_name="us-east-1")
    resp = client.get_secret_value(SecretId=CF_SECRET_ARN)
    pem_data = resp["SecretString"].encode("utf-8")
    _private_key = serialization.load_pem_private_key(pem_data, password=None)
    return _private_key


def _rsa_sign(message: bytes) -> bytes:
    """Sign a message with the CloudFront private key (RSA SHA-1, per CF spec)."""
    key = _get_private_key()
    return key.sign(message, padding.PKCS1v15(), hashes.SHA1())


def _cf_base64(data: bytes) -> str:
    """CloudFront-safe base64 encoding (replace +, =, /)."""
    return base64.b64encode(data).decode("ascii").replace("+", "-").replace("=", "_").replace("/", "~")


def generate_signed_url(s3_key: str, expires_in: int = 300) -> str:
    """Generate a CloudFront signed URL for an S3 object.

    Args:
        s3_key: The S3 object key (e.g., 'reports/RPT-123.html')
        expires_in: URL validity in seconds (default 300 = 5 minutes)

    Returns:
        Signed CloudFront URL string
    """
    url = f"https://{CF_DISTRIBUTION_DOMAIN}/{s3_key}"
    expires = int((datetime.now(timezone.utc) + timedelta(seconds=expires_in)).timestamp())

    # Canned policy
    policy_statement = f'{{"Statement":[{{"Resource":"{url}","Condition":{{"DateLessThan":{{"AWS:EpochTime":{expires}}}}}}}]}}'
    signature = _rsa_sign(policy_statement.encode("utf-8"))

    signed_url = (
        f"{url}"
        f"?Expires={expires}"
        f"&Signature={_cf_base64(signature)}"
        f"&Key-Pair-Id={CF_KEY_PAIR_ID}"
    )
    return signed_url
