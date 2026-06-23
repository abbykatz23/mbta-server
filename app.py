import base64
import io
import os
import re
import uuid
from datetime import datetime, timezone

import boto3
from fastapi import FastAPI, Header, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from PIL import Image
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://*.netlify.app"],
    allow_methods=["*"],
    allow_headers=["*"],
)

S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
PI_API_KEY = os.environ["PI_API_KEY"]

SPRITE_WIDTH = 26
SPRITE_HEIGHT = 5
MAX_FILE_BYTES = 10_000

s3 = boto3.client("s3")
dynamo = boto3.resource("dynamodb")
table = dynamo.Table(DYNAMODB_TABLE)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def require_pi_key(x_api_key: str = Header(...)):
    if x_api_key != PI_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Models ────────────────────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    name: str
    birthday: str       # "MM-DD"
    pngData: str        # data URL: "data:image/png;base64,..."


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_birthday(birthday: str):
    if not re.fullmatch(r"\d{2}-\d{2}", birthday):
        raise HTTPException(status_code=400, detail="birthday must be MM-DD")
    month, day = int(birthday[:2]), int(birthday[3:])
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="birthday month out of range")
    days_in_month = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not (1 <= day <= days_in_month[month]):
        raise HTTPException(status_code=400, detail="birthday day out of range")


def _decode_png(png_data: str) -> bytes:
    prefix = "data:image/png;base64,"
    if not png_data.startswith(prefix):
        raise HTTPException(status_code=400, detail="pngData must be a PNG data URL")
    try:
        return base64.b64decode(png_data[len(prefix):])
    except Exception:
        raise HTTPException(status_code=400, detail="pngData base64 is invalid")


def _validate_png_bytes(png_bytes: bytes) -> None:
    if len(png_bytes) > MAX_FILE_BYTES:
        raise HTTPException(status_code=400, detail=f"PNG must be under {MAX_FILE_BYTES} bytes")
    try:
        img = Image.open(io.BytesIO(png_bytes))
        w, h = img.size
    except Exception:
        raise HTTPException(status_code=400, detail="Could not open PNG")
    if h != SPRITE_HEIGHT or not (1 <= w <= SPRITE_WIDTH):
        raise HTTPException(
            status_code=400,
            detail=f"PNG must be {SPRITE_HEIGHT} px tall and 1–{SPRITE_WIDTH} px wide, got {w}×{h}"
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/submit", status_code=201)
def submit(body: SubmitRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if len(name) > 60:
        raise HTTPException(status_code=400, detail="name must be 60 characters or fewer")

    _validate_birthday(body.birthday)
    png_bytes = _decode_png(body.pngData)
    _validate_png_bytes(png_bytes)

    submission_id = str(uuid.uuid4())
    s3_key = f"sprites/{submission_id}.png"
    submitted_at = datetime.now(timezone.utc).isoformat()

    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=png_bytes, ContentType="image/png")

    table.put_item(Item={
        "id": submission_id,
        "name": name,
        "birthday": body.birthday,
        "status": "approved",
        "s3_key": s3_key,
        "submitted_at": submitted_at,
        "approved_at": submitted_at,
    })

    return {"id": submission_id, "status": "approved"}


@app.get("/sprites")
def get_sprites(since: str = None, x_api_key: str = Header(...)):
    require_pi_key(x_api_key)

    scan_kwargs = {
        "FilterExpression": boto3.dynamodb.conditions.Attr("status").eq("approved")
    }
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since must be an ISO timestamp")
        scan_kwargs["FilterExpression"] &= boto3.dynamodb.conditions.Attr("approved_at").gte(since)

    response = table.scan(**scan_kwargs)
    items = response.get("Items", [])

    result = []
    for item in items:
        png_obj = s3.get_object(Bucket=S3_BUCKET, Key=item["s3_key"])
        png_bytes = png_obj["Body"].read()
        result.append({
            "id": item["id"],
            "name": item["name"],
            "birthday": item["birthday"],
            "png_base64": base64.b64encode(png_bytes).decode(),
        })

    return result


@app.delete("/submissions/{submission_id}", status_code=204)
def delete_submission(submission_id: str = Path(...), x_api_key: str = Header(...)):
    require_pi_key(x_api_key)

    response = table.get_item(Key={"id": submission_id})
    if not response.get("Item"):
        raise HTTPException(status_code=404, detail="Submission not found")

    s3_key = response["Item"]["s3_key"]
    s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
    table.delete_item(Key={"id": submission_id})


handler = Mangum(app, lifespan="off", api_gateway_base_path=None)
