# OmniDesk Backend — Deployment Guide

> Quick reference for deploying Lambda functions and API Gateway.

## Environment Setup

```bash
export AWS_SHARED_CREDENTIALS_FILE=/Users/dhruvsharma/Documents/Research/OmniDesk/.aws/credentials
export AWS_CONFIG_FILE=/Users/dhruvsharma/Documents/Research/OmniDesk/.aws/config
```

## AWS Resource IDs

| Resource | ID / ARN |
|----------|----------|
| AWS Account | `577397739686` |
| Region | `us-east-1` |
| Lambda Role | `arn:aws:iam::577397739686:role/omnidesk-lambda-role` |
| Lambda Layer | `arn:aws:lambda:us-east-1:577397739686:layer:omnidesk-shared-layer:2` |
| API Gateway ID | `zak2w9nuuh` |
| API Gateway URL | `https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev` |
| S3 Bucket | `omnidesk-files-577397739686` |

### API Gateway Resource IDs

| Path | Resource ID | Method | Lambda |
|------|------------|--------|--------|
| `/api/auth/register` | `gw65xt` | POST | `omnidesk-auth-register` |
| `/api/auth/login` | `d2atka` | POST | `omnidesk-auth-login` |
| `/api/auth/me` | `t2ychm` | GET | `omnidesk-auth-me` |
| `/mcp` | `fsr82g` | POST | `omnidesk-mcp-server` |
| `/api/categories` | `1e27t0` | GET, POST | `omnidesk-categories` |
| `/api/categories/{id}` | `jrt9r5` | GET | `omnidesk-categories` |
| `/api/products` | `dmil9x` | GET, POST | `omnidesk-product-list`, `omnidesk-product-create` |
| `/api/products/{id}` | `t8xk6z` | GET, PUT | `omnidesk-product-list`, `omnidesk-product-update` |
| `/api/products/{id}/deactivate` | `9fj13j` | PATCH | `omnidesk-product-update` |
| `/api/products/search` | `ai071f` | GET | `omnidesk-product-list` |
| `/api/warehouses` | `yuordm` | GET, POST | `omnidesk-warehouses` |
| `/api/warehouses/{id}` | `j0gcmr` | GET | `omnidesk-warehouses` |
| `/api/stock/{product_id}` | `dwzody` | GET | `omnidesk-stock-check` |
| `/api/stock/adjust` | `qtek0v` | POST | `omnidesk-stock-adjust` |
| `/api/stock/low` | `h7113j` | GET | `omnidesk-stock-low` |
| `/api/stock/movements/{product_id}` | `7a580d` | GET | `omnidesk-stock-movements` |
| `/api` | `bdbaw3` | — | — |
| `/api/auth` | `xcx7sm` | — | — |
| `/api/stock` | `pbghbw` | — | — |
| `/api/stock/movements` | `in9yz8` | — | — |
| Root `/` | `683b9dp63l` | — | — |

### IAM Role Policies

| Policy | Purpose |
|--------|---------|
| `AWSLambdaBasicExecutionRole` | CloudWatch Logs |
| `AmazonDynamoDBFullAccess` | DynamoDB tables |
| `AmazonS3FullAccess` | S3 bucket |
| `SecretsManagerReadWrite` | DB credentials, JWT secret |

### Lambda Environment Variables

```
SECRETS_ARN      = omnidesk/db-credentials
JWT_SECRET_ARN   = omnidesk/jwt-secret
S3_BUCKET        = omnidesk-files-577397739686
AUDIT_TABLE      = omnidesk-audit-log
```

---

## 1. Update Lambda Layer (when dependencies change)

```bash
# Install Linux arm64 binaries (MUST use --platform for Lambda compatibility)
rm -rf /tmp/omnidesk-layer/python
mkdir -p /tmp/omnidesk-layer/python
pip3 install --platform manylinux2014_aarch64 --only-binary=:all: \
  --target /tmp/omnidesk-layer/python \
  psycopg2-binary pyjwt bcrypt requests

# Zip and publish
cd /tmp/omnidesk-layer
zip -r /tmp/omnidesk-layer.zip python/
aws lambda publish-layer-version \
  --layer-name omnidesk-shared-layer \
  --zip-file fileb:///tmp/omnidesk-layer.zip \
  --compatible-runtimes python3.11 \
  --compatible-architectures arm64 \
  --region us-east-1

# Update all Lambdas to use new layer version (replace :N with new version number)
LAYER_ARN="arn:aws:lambda:us-east-1:577397739686:layer:omnidesk-shared-layer:N"
for FUNC in omnidesk-auth-register omnidesk-auth-login omnidesk-auth-me; do
  aws lambda update-function-configuration --function-name "$FUNC" --layers "$LAYER_ARN" --region us-east-1
done
```

> **IMPORTANT**: Always use `--platform manylinux2014_aarch64 --only-binary=:all:` when building the layer on macOS. Without this, native binaries (bcrypt, psycopg2) will be macOS-compiled and fail on Lambda with `invalid ELF header`.

---

## 2. Deploy / Update a Lambda Function

### Package a Lambda

Each Lambda needs its handler file renamed to `lambda_function.py` + the `utils/` directory.

```bash
cd /Users/dhruvsharma/Documents/Research/OmniDesk/backend

# Example: package auth-register
FUNC_NAME="auth-register"
SOURCE_FILE="lambdas/auth/register.py"

mkdir -p /tmp/omnidesk-deploy/$FUNC_NAME
cp "$SOURCE_FILE" /tmp/omnidesk-deploy/$FUNC_NAME/lambda_function.py
cp -r utils /tmp/omnidesk-deploy/$FUNC_NAME/utils
cd /tmp/omnidesk-deploy/$FUNC_NAME
zip -r /tmp/omnidesk-$FUNC_NAME.zip .
```

### Create a new Lambda

```bash
aws lambda create-function \
  --function-name omnidesk-$FUNC_NAME \
  --runtime python3.11 \
  --architectures arm64 \
  --handler lambda_function.lambda_handler \
  --role "arn:aws:iam::577397739686:role/omnidesk-lambda-role" \
  --zip-file fileb:///tmp/omnidesk-$FUNC_NAME.zip \
  --layers "arn:aws:lambda:us-east-1:577397739686:layer:omnidesk-shared-layer:2" \
  --memory-size 256 \
  --timeout 30 \
  --environment '{"Variables":{"SECRETS_ARN":"omnidesk/db-credentials","JWT_SECRET_ARN":"omnidesk/jwt-secret","S3_BUCKET":"omnidesk-files-577397739686","AUDIT_TABLE":"omnidesk-audit-log"}}' \
  --region us-east-1
```

### Update existing Lambda code

```bash
aws lambda update-function-code \
  --function-name omnidesk-$FUNC_NAME \
  --zip-file fileb:///tmp/omnidesk-$FUNC_NAME.zip \
  --region us-east-1
```

### Lambda without shared layer (e.g., MCP server)

MCP server has no external deps — skip `--layers` and `utils/` copy:

```bash
mkdir -p /tmp/omnidesk-deploy/mcp-server
cp lambdas/mcp/server.py /tmp/omnidesk-deploy/mcp-server/lambda_function.py
cd /tmp/omnidesk-deploy/mcp-server
zip -r /tmp/omnidesk-mcp-server.zip .

aws lambda update-function-code \
  --function-name omnidesk-mcp-server \
  --zip-file fileb:///tmp/omnidesk-mcp-server.zip \
  --region us-east-1
```

---

## 3. Add a New API Gateway Route

```bash
API_ID="zak2w9nuuh"
REGION="us-east-1"
ACCT="577397739686"
PARENT_ID="<parent-resource-id>"   # e.g., xcx7sm for /api/auth

# Step 1: Create resource
aws apigateway create-resource \
  --rest-api-id $API_ID \
  --parent-id $PARENT_ID \
  --path-part "new-path" \
  --region $REGION

# Step 2: Create method (use GET, POST, PUT, PATCH as needed)
aws apigateway put-method \
  --rest-api-id $API_ID \
  --resource-id <new-resource-id> \
  --http-method POST \
  --authorization-type NONE \
  --region $REGION

# Step 3: Create Lambda proxy integration
aws apigateway put-integration \
  --rest-api-id $API_ID \
  --resource-id <new-resource-id> \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri "arn:aws:apigateway:$REGION:lambda:path/2015-03-31/functions/arn:aws:lambda:$REGION:$ACCT:function:<function-name>/invocations" \
  --region $REGION

# Step 4: Grant API Gateway permission to invoke Lambda
aws lambda add-permission \
  --function-name <function-name> \
  --statement-id "apigateway-<resource-id>-POST" \
  --action lambda:InvokeFunction \
  --principal apigateway.amazonaws.com \
  --source-arn "arn:aws:execute-api:$REGION:$ACCT:$API_ID/*/POST/*" \
  --region $REGION

# Step 5: Add CORS OPTIONS method
aws apigateway put-method \
  --rest-api-id $API_ID --resource-id <new-resource-id> \
  --http-method OPTIONS --authorization-type NONE --region $REGION

aws apigateway put-integration \
  --rest-api-id $API_ID --resource-id <new-resource-id> \
  --http-method OPTIONS --type MOCK \
  --request-templates '{"application/json":"{\"statusCode\": 200}"}' --region $REGION

aws apigateway put-method-response \
  --rest-api-id $API_ID --resource-id <new-resource-id> \
  --http-method OPTIONS --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":false,"method.response.header.Access-Control-Allow-Methods":false,"method.response.header.Access-Control-Allow-Origin":false}' \
  --region $REGION

aws apigateway put-integration-response \
  --rest-api-id $API_ID --resource-id <new-resource-id> \
  --http-method OPTIONS --status-code 200 \
  --response-parameters '{"method.response.header.Access-Control-Allow-Headers":"'"'"'Content-Type,Authorization,Mcp-Session-Id'"'"'","method.response.header.Access-Control-Allow-Methods":"'"'"'GET,POST,PUT,PATCH,OPTIONS'"'"'","method.response.header.Access-Control-Allow-Origin":"'"'"'*'"'"'"}' \
  --region $REGION

# Step 6: Deploy
aws apigateway create-deployment \
  --rest-api-id $API_ID \
  --stage-name dev \
  --region $REGION
```

---

## 4. Redeploy API Gateway (after route changes)

```bash
aws apigateway create-deployment \
  --rest-api-id zak2w9nuuh \
  --stage-name dev \
  --description "Description of changes" \
  --region us-east-1
```

---

## 5. Debugging

### Check CloudWatch Logs

```bash
# Get latest log stream for a function
LOG_STREAM=$(aws logs describe-log-streams \
  --log-group-name /aws/lambda/omnidesk-auth-register \
  --order-by LastEventTime --descending --limit 1 \
  --region us-east-1 --query 'logStreams[0].logStreamName' --output text)

aws logs get-log-events \
  --log-group-name /aws/lambda/omnidesk-auth-register \
  --log-stream-name "$LOG_STREAM" \
  --region us-east-1 \
  --query 'events[*].message' --output text
```

### Test invoke Lambda directly

```bash
aws lambda invoke \
  --function-name omnidesk-auth-register \
  --payload '{"httpMethod":"POST","body":"{\"email\":\"test@x.com\",\"password\":\"12345678\",\"full_name\":\"Test\"}"}' \
  --region us-east-1 \
  /tmp/lambda-response.json && cat /tmp/lambda-response.json
```

### Test endpoints with curl

```bash
BASE="https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev"

# Register
curl -s -X POST "$BASE/api/auth/register" -H "Content-Type: application/json" \
  -d '{"email":"x@x.com","password":"12345678","full_name":"Test"}'

# Login
curl -s -X POST "$BASE/api/auth/login" -H "Content-Type: application/json" \
  -d '{"email":"x@x.com","password":"12345678"}'

# Me (replace TOKEN)
curl -s -X GET "$BASE/api/auth/me" -H "Authorization: Bearer TOKEN"

# MCP initialize
curl -s -X POST "$BASE/mcp" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# MCP tools/list
curl -s -X POST "$BASE/mcp" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

---

## 6. MCP Token Management

### Generate a new 48h token

```bash
curl -s -X POST https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@omnidesk.test","password":"Admin@1234"}'
# Copy the access_token from the JSON response
```

### Update Claude Desktop config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
"omnidesk": {
  "command": "npx",
  "args": [
    "mcp-remote",
    "https://zak2w9nuuh.execute-api.us-east-1.amazonaws.com/dev/mcp",
    "--header",
    "Authorization: Bearer <paste-token-here>"
  ]
}
```

Restart Claude Desktop after updating.

### Token details

| Setting | Value |
|---------|-------|
| Access token expiry | 48 hours |
| Refresh token expiry | 30 days |
| Algorithm | HS256 |
| Secret | AWS Secrets Manager `omnidesk/jwt-secret` |
| Claims | `user_id`, `email`, `role`, `type`, `iat`, `exp` |

### When token expires

MCP tools return: `"error": "Authentication required. Your token is missing or expired."`
Regenerate using the curl command above and restart Claude Desktop.

---

## Gotchas & Lessons Learned

1. **Layer binary compatibility**: Always build with `--platform manylinux2014_aarch64 --only-binary=:all:` on macOS. Native macOS .so files cause `invalid ELF header` on Lambda.
2. **IAM role propagation**: Wait ~10 seconds after creating a role before creating Lambda functions, or you'll get `The role defined for the function cannot be assumed by Lambda`.
3. **API Gateway integration URI**: Must be the full invocation URI format: `arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/{function-arn}/invocations`. Shortened ARNs fail with "must contain path or action".
4. **Lambda handler path**: Handler is always `lambda_function.lambda_handler` — the source file must be renamed to `lambda_function.py` in the zip.
5. **utils/ packaging**: Every Lambda zip must include the `utils/` directory.
6. **mcp-remote needs GET+DELETE**: API Gateway `/mcp` must have GET, POST, DELETE, and OPTIONS methods. `mcp-remote` does a Streamable HTTP handshake via GET before sending POST. Missing GET causes 403 "Missing Authentication Token".
7. **No login tool in MCP**: Token is passed via `--header` in Claude Desktop config, not via a chat-based login flow. User never types credentials in chat.
