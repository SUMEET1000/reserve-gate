"""Authenticate Razorpay webhooks before parsing or touching the ledger."""
import hashlib
import hmac
import json
import os

from starlette.responses import JSONResponse

from . import audit, ledger

MAX_BODY_BYTES = 1_000_000


def validate(body: bytes, signature: str | None, event_id: str | None,
             secret: str | None) -> tuple[int, str, dict | None]:
    """Verify raw bytes before JSON decoding. Returns status, reason, payload."""
    if not secret:
        return 503, "secret_not_configured", None
    if not signature:
        return 401, "missing_signature", None
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return 401, "invalid_signature", None
    if not event_id or not event_id.strip():
        return 400, "missing_event_id", None
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, "malformed_json", None
    if not isinstance(payload, dict):
        return 400, "malformed_json", None
    return 200, "authenticated", payload


async def handle(request):
    event_id = request.headers.get("x-razorpay-event-id")
    signature = request.headers.get("x-razorpay-signature")
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        status, reason, _ = validate(b"", signature, event_id, secret)
        audit.record(event="webhook_reject", event_id=event_id, reason=reason)
        errors = {401: "invalid signature", 503: "webhook unavailable"}
        return JSONResponse({"error": errors[status]}, status)

    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            audit.record(event="webhook_reject", event_id=event_id, reason="body_too_large")
            return JSONResponse({"error": "webhook body too large"}, 413)
        chunks.append(chunk)
    body = b"".join(chunks)
    status, reason, payload = validate(body, signature, event_id, secret)
    if status != 200:
        audit.record(event="webhook_reject", event_id=event_id, reason=reason)
        errors = {401: "invalid signature", 400: "malformed webhook", 503: "webhook unavailable"}
        return JSONResponse({"error": errors[status]}, status)

    event_type = payload.get("event")
    if not isinstance(event_type, str):
        event_type = "unknown"
    wrapped = payload.get("payload")
    payment_part = wrapped.get("payment") if isinstance(wrapped, dict) else None
    payment = payment_part.get("entity") if isinstance(payment_part, dict) else None
    conn = ledger.connect()
    try:
        result = ledger.reconcile_webhook(conn, event_id, event_type, payment)
    except Exception:
        audit.record(event="webhook_error", event_id=event_id, event_type=event_type)
        return JSONResponse({"error": "internal error"}, 500)
    finally:
        conn.close()
    effect = result["effect"]
    print("WEBHOOK " + json.dumps({"event": event_type, "effect": effect,
                                    "reason": result["reason"]}, separators=(",", ":")),
          flush=True)
    return JSONResponse({key: result[key] for key in ("accepted", "applied", "reason")})
