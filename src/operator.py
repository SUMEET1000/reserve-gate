"""Authenticated recovery views; payment decisions remain in the ledger."""
import secrets

from starlette.responses import JSONResponse

from . import audit, ledger
from .upstream import UpstreamError, call_razorpay


def reply(body, status=200):
    return JSONResponse(body, status, headers={"Cache-Control": "no-store"})


async def inbox(request):
    conn = ledger.connect()
    try:
        ledger.sweep_expired(conn)
        conn.execute("BEGIN")
        reservations = [dict(r) for r in conn.execute(
            "SELECT r.reservation_id, r.block_id, r.order_id, r.payment_id, r.amount,"
            " r.currency, r.state, r.created_at, r.expires_at, r.settled_at,"
            " r.outcome_unknown, r.pending_call IS NOT NULL AS awaiting_approval,"
            " b.caller_id, b.frozen_at, b.revoked_at, b.expires_at AS block_expires_at"
            " FROM reservations r JOIN blocks b ON b.block_id = r.block_id"
            " WHERE r.state = 'held' AND (r.pending_call IS NOT NULL OR r.outcome_unknown = 1)"
            " ORDER BY r.created_at, r.reservation_id")]
        blocks = [dict(b) for b in conn.execute(
            "SELECT * FROM blocks WHERE frozen_at IS NOT NULL ORDER BY frozen_at, block_id")]
        for b in blocks:
            b["history"] = [dict(r) for r in conn.execute(
                "SELECT reservation_id, order_id, payment_id, amount, currency, state,"
                " created_at, settled_at, outcome_unknown FROM reservations"
                " WHERE block_id = ? ORDER BY created_at DESC, reservation_id LIMIT 20",
                (b["block_id"],))]
        conn.commit()
    finally:
        conn.close()
    return reply({"checked_at": ledger.iso(ledger.now_utc()),
                  "reservations": reservations, "blocks": blocks})


async def recover(request):
    """Read provider evidence, then reuse the authenticated settlement rules.

    A non-captured payment does not prove that its order cannot be paid later.
    No lookup failure or non-captured status releases the reservation.
    """
    rid = request.path_params["reservation_id"]
    conn = ledger.connect()
    try:
        row = conn.execute("SELECT * FROM reservations WHERE reservation_id = ?", (rid,)).fetchone()
        if row is None:
            return reply({"error": "Reservation not found."}, 404)
        if row["state"] != "held" or not row["outcome_unknown"]:
            return reply({"error": "This reservation no longer needs recovery. Refresh the inbox."}, 409)
        if not row["payment_id"] or not row["order_id"]:
            return reply({"error": "The payment or order reference is missing. Review the provider records; funds remain reserved."}, 409)
        try:
            payment = await call_razorpay("fetch_payment", {"payment_id": row["payment_id"]})
        except UpstreamError:
            audit.record(event="operator_recovery_unavailable", reservation_id=rid)
            return reply({"error": "Provider lookup failed. Funds remain reserved. Try again later."}, 502)
        valid = (isinstance(payment, dict) and payment.get("id") == row["payment_id"]
                 and payment.get("order_id") == row["order_id"]
                 and type(payment.get("amount")) is int and payment["amount"] == row["amount"]
                 and isinstance(payment.get("currency"), str)
                 and payment["currency"].upper() == row["currency"].upper()
                 and isinstance(payment.get("status"), str))
        audit.record(event="operator_recovery_checked", reservation_id=rid,
                     payment_id=row["payment_id"], matching=valid,
                     status=payment.get("status") if isinstance(payment, dict) else None)
        if not valid:
            return reply({"error": "Provider evidence does not match this reservation. Funds remain reserved; review the conflict."}, 409)
        evidence = {k: payment[k] for k in ("id", "order_id", "amount", "currency", "status")}
        if payment["status"] != "captured":
            return reply({"evidence": evidence, "message": "Capture is not confirmed. Funds remain reserved."})
        # A new observation gets its own event id. The ledger deduplicates the
        # payment itself and rechecks conflicts under its write transaction.
        result = ledger.reconcile_webhook(conn, "operator:" + secrets.token_hex(16),
                                          "payment.captured", evidence)
        audit.record(event="operator_recovery_result", reservation_id=rid, **result)
        return reply({"evidence": evidence, "result": result,
                      "message": ("Captured payment reconciled." if result["effect"] == "APPLY"
                                  else "Ledger result: " + result["reason"] + ". Review the current state.")},
                     409 if result["effect"] == "REJECT" else 200)
    finally:
        conn.close()


def install(mcp):
    mcp.custom_route("/operator/inbox", ["GET"])(inbox)
    mcp.custom_route("/operator/recover/{reservation_id}", ["POST"])(recover)
