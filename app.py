import base64
import hmac
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

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")]

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "X-API-Key"],
)

S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
PI_API_KEY = os.environ["PI_API_KEY"]

SPRITE_WIDTH = 26
SPRITE_HEIGHT = 5
MAX_FILE_BYTES = 10_000
MAX_SUBMISSIONS = 500

HARDCODED_SPECIAL_TRAINS = {
    "january": {"birthday_month": 1},
    "february": {"birthday_month": 2},
    "march": {"birthday_month": 3},
    "april": {"birthday_month": 4},
    "may": {"birthday_month": 5},
    "june": {"birthday_month": 6},
    "july": {"birthday_month": 7},
    "august": {"birthday_month": 8},
    "september": {"birthday_month": 9},
    "october": {"birthday_month": 10},
    "november": {"birthday_month": 11},
    "december": {"birthday_month": 12},
    "roommates": {"birthday": "08-01"},
}

SPECIAL_TRAIN_QUEUE_ID = "__special_queue__"

s3 = boto3.client("s3")
dynamo = boto3.resource("dynamodb")
table = dynamo.Table(DYNAMODB_TABLE)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def require_pi_key(x_api_key: str):
    if not hmac.compare_digest(x_api_key, PI_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Models ────────────────────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    name: str
    birthday: str       # "MM-DD"
    pngData: str        # data URL: "data:image/png;base64,..."
    flip_rtl: bool = True


_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _validate_uuid(value: str):
    if not _UUID_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid ID format")


def _scan_all(table, **kwargs):
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(**{**kwargs, "ExclusiveStartKey": resp["LastEvaluatedKey"]})
        items.extend(resp.get("Items", []))
    return items


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
        if img.format != "PNG":
            raise HTTPException(status_code=400, detail="File must be a PNG")
        w, h = img.size
    except HTTPException:
        raise
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
    name = re.sub(r'[^\x20-\x7E]', '', name).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    count_resp = table.scan(
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("approved"),
        Select="COUNT",
    )
    if count_resp.get("Count", 0) >= MAX_SUBMISSIONS:
        raise HTTPException(status_code=503, detail="Submission limit reached. Try again later.")

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
        "updated_at": submitted_at,
        "flip_rtl": body.flip_rtl,
    })

    return {"id": submission_id, "status": "approved"}


@app.get("/sprites")
def get_sprites(since: str | None = None, x_api_key: str = Header(...)):
    require_pi_key(x_api_key)

    scan_kwargs = {
        "FilterExpression": boto3.dynamodb.conditions.Attr("status").eq("approved")
    }
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="since must be an ISO timestamp")
        from boto3.dynamodb.conditions import Attr
        scan_kwargs["FilterExpression"] &= (Attr("approved_at").gte(since) | Attr("updated_at").gte(since))

    items = _scan_all(table, **scan_kwargs)

    result = []
    for item in items:
        png_obj = s3.get_object(Bucket=S3_BUCKET, Key=item["s3_key"])
        png_bytes = png_obj["Body"].read()
        result.append({
            "id": item["id"],
            "name": item["name"],
            "birthday": item["birthday"],
            "png_base64": base64.b64encode(png_bytes).decode(),
            "flip_rtl": item.get("flip_rtl", True),
        })

    return result


@app.delete("/submissions/{submission_id}", status_code=204)
def delete_submission(submission_id: str = Path(...), x_api_key: str = Header(...)):
    require_pi_key(x_api_key)
    _validate_uuid(submission_id)

    response = table.get_item(Key={"id": submission_id})
    if not response.get("Item"):
        raise HTTPException(status_code=404, detail="Submission not found")

    s3_key = response["Item"]["s3_key"]
    s3.delete_object(Bucket=S3_BUCKET, Key=s3_key)
    table.delete_item(Key={"id": submission_id})


@app.get("/submissions")
def list_submissions():
    items = _scan_all(
        table,
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("approved")
    )
    result = []
    for item in items:
        png_obj = s3.get_object(Bucket=S3_BUCKET, Key=item["s3_key"])
        png_bytes = png_obj["Body"].read()
        result.append({
            "id": item["id"],
            "name": item["name"],
            "birthday": item.get("birthday", ""),
            "submitted_at": item.get("submitted_at", ""),
            "png_base64": base64.b64encode(png_bytes).decode(),
            "flip_rtl": item.get("flip_rtl", True),
        })
    return sorted(result, key=lambda x: x["submitted_at"])


class UpdateRequest(BaseModel):
    name: str | None = None
    birthday: str | None = None
    flip_rtl: bool | None = None


@app.patch("/submissions/{submission_id}")
def update_submission(body: UpdateRequest, submission_id: str = Path(...), x_api_key: str = Header(...)):
    require_pi_key(x_api_key)
    _validate_uuid(submission_id)

    response = table.get_item(Key={"id": submission_id})
    if not response.get("Item"):
        raise HTTPException(status_code=404, detail="Submission not found")

    expr_parts, expr_values, expr_names = [], {}, {}
    expr_parts.append("updated_at = :updated_at")
    expr_values[":updated_at"] = datetime.now(timezone.utc).isoformat()
    if body.name is not None:
        name = body.name.strip()
        if not name or len(name) > 60:
            raise HTTPException(status_code=400, detail="Invalid name")
        expr_parts.append("#n = :name")
        expr_names["#n"] = "name"
        expr_values[":name"] = name
    if body.birthday is not None:
        _validate_birthday(body.birthday)
        expr_parts.append("#b = :birthday")
        expr_names["#b"] = "birthday"
        expr_values[":birthday"] = body.birthday
    if body.flip_rtl is not None:
        expr_parts.append("flip_rtl = :flip_rtl")
        expr_values[":flip_rtl"] = body.flip_rtl
    if len(expr_parts) == 1:
        raise HTTPException(status_code=400, detail="Nothing to update")

    kwargs = {
        "Key": {"id": submission_id},
        "UpdateExpression": "SET " + ", ".join(expr_parts),
        "ExpressionAttributeValues": expr_values,
    }
    if expr_names:
        kwargs["ExpressionAttributeNames"] = expr_names
    table.update_item(**kwargs)
    return {"id": submission_id, "status": "updated"}


@app.post("/queue/{submission_id}")
def queue_submission(submission_id: str = Path(...), x_api_key: str = Header(...)):
    require_pi_key(x_api_key)
    _validate_uuid(submission_id)

    response = table.get_item(Key={"id": submission_id})
    if not response.get("Item"):
        raise HTTPException(status_code=404, detail="Submission not found")

    table.update_item(
        Key={"id": submission_id},
        UpdateExpression="SET queued = :true",
        ExpressionAttributeValues={":true": True},
    )
    return {"id": submission_id, "queued": True}


@app.post("/queued/consume")
def get_queued(x_api_key: str = Header(...)):
    require_pi_key(x_api_key)

    items = _scan_all(
        table,
        FilterExpression=boto3.dynamodb.conditions.Attr("queued").eq(True)
    )
    ids = [item["id"] for item in items]

    for item in items:
        table.update_item(
            Key={"id": item["id"]},
            UpdateExpression="REMOVE queued",
        )

    return {"ids": ids}


@app.get("/special-trains")
def list_special_trains():
    return [{"name": name, **meta} for name, meta in HARDCODED_SPECIAL_TRAINS.items()]


@app.post("/queue-special/{name}")
def queue_special_train(name: str = Path(...), x_api_key: str = Header(...)):
    require_pi_key(x_api_key)
    if name not in HARDCODED_SPECIAL_TRAINS:
        raise HTTPException(status_code=404, detail="Unknown special train")
    table.update_item(
        Key={"id": SPECIAL_TRAIN_QUEUE_ID},
        UpdateExpression="SET queued_names = list_append(if_not_exists(queued_names, :empty), :name)",
        ExpressionAttributeValues={":empty": [], ":name": [name]},
    )
    return {"name": name, "queued": True}


@app.post("/queued-special/consume")
def consume_queued_special(x_api_key: str = Header(...)):
    require_pi_key(x_api_key)
    response = table.get_item(Key={"id": SPECIAL_TRAIN_QUEUE_ID})
    item = response.get("Item", {})
    names = list(item.get("queued_names", []))
    if names:
        table.update_item(
            Key={"id": SPECIAL_TRAIN_QUEUE_ID},
            UpdateExpression="REMOVE queued_names",
        )
    return {"names": names}


handler = Mangum(app, lifespan="off", api_gateway_base_path=None)
