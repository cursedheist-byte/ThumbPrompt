"""
Razorpay payment orchestration.

Two responsibilities, kept strictly separate from app.py so no Flask
objects appear here:

  1. Order creation - the server is the ONLY thing that decides price,
     plan, and generation counts. The client selects a plan id (or a
     top-up quantity); everything else - amount in paise, currency,
     which internal user it belongs to - is computed here from
     config.Config.PLANS / config.Config.TOPUP, never from client input.

  2. Payment verification - verifies the Razorpay checkout signature
     server-side, then (only on success) grants credits via an atomic,
     idempotent database update. See database.apply_verified_plan_payment
     / apply_verified_topup_payment for the idempotency mechanism
     (payments.razorpay_order_id transitions 'created' -> 'paid' exactly
     once; a second verify call for the same order is a safe no-op).
"""

import time

import razorpay

from config import Config
from database import database


class PaymentServiceError(Exception):
    """User-facing payment error: bad plan id, not eligible for a
    top-up, Razorpay not configured, signature verification failed,
    etc. Routes turn this into a 400 JSON response."""


def _client():
    if not Config.razorpay_configured():
        raise PaymentServiceError(
            "Payments are not configured on this server yet. Set "
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET."
        )
    return razorpay.Client(auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET))


# ---------------------------------------------------------------------
# Order creation
# ---------------------------------------------------------------------

def create_plan_order(user, plan_id):
    """Creates a Razorpay order for a one-time paid-plan purchase. The
    amount is computed server-side from Config.PLANS[plan_id] - a client
    cannot influence the price by sending its own amount/plan payload."""
    if not plan_id or plan_id not in Config.PLANS or plan_id == Config.DEFAULT_PLAN:
        raise PaymentServiceError("Unknown or invalid plan.")

    plan = Config.PLANS[plan_id]
    amount_paise = round(plan["price_inr"] * 100)
    if amount_paise <= 0:
        raise PaymentServiceError("That plan isn't purchasable.")

    client = _client()
    order = client.order.create(
        data={
            "amount": amount_paise,
            "currency": "INR",
            "notes": {
                # Notes are for auditing/reconciliation only - verification
                # below re-derives everything it needs from our own
                # payments row, never trusts these back from the client.
                "user_id": user["id"],
                "kind": "plan",
                "plan": plan_id,
            },
        }
    )

    database.create_payment_order(
        user_id=user["id"],
        razorpay_order_id=order["id"],
        kind="plan",
        amount_paise=amount_paise,
        currency="INR",
        plan=plan_id,
        topup_quantity=None,
    )

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": Config.RAZORPAY_KEY_ID,
        "kind": "plan",
        "plan": plan_id,
    }


def create_topup_order(user, quantity):
    """Creates a Razorpay order for a ₹1/generation top-up. Only allowed
    while the user has an active (non-expired) paid plan. `quantity` is
    whatever the client asked for, but is validated and clamped here -
    the resulting amount is always server-computed (price_inr * quantity
    * 100), never accepted from the client."""
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise PaymentServiceError("Invalid top-up quantity.")

    if quantity < 1 or quantity > Config.TOPUP["max_quantity"]:
        raise PaymentServiceError(
            f"Top-up quantity must be between 1 and {Config.TOPUP['max_quantity']}."
        )

    plan_expires_at = user.get("plan_expires_at")
    has_active_paid_plan = (
        user["plan"] != Config.DEFAULT_PLAN
        and plan_expires_at is not None
        and plan_expires_at > time.time()
    )
    if not has_active_paid_plan:
        raise PaymentServiceError(
            "Top-ups are only available while you have an active paid plan."
        )

    amount_paise = round(Config.TOPUP["price_inr"] * 100) * quantity

    client = _client()
    order = client.order.create(
        data={
            "amount": amount_paise,
            "currency": "INR",
            "notes": {
                "user_id": user["id"],
                "kind": "topup",
                "quantity": str(quantity),
            },
        }
    )

    database.create_payment_order(
        user_id=user["id"],
        razorpay_order_id=order["id"],
        kind="topup",
        amount_paise=amount_paise,
        currency="INR",
        plan=None,
        topup_quantity=quantity,
    )

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": Config.RAZORPAY_KEY_ID,
        "kind": "topup",
        "topup_quantity": quantity,
    }


# ---------------------------------------------------------------------
# Verification + credit granting
# ---------------------------------------------------------------------

def verify_and_apply(user, razorpay_order_id, razorpay_payment_id, razorpay_signature):
    """Verifies a Razorpay checkout signature and, only on success,
    grants the plan/top-up credits tied to that order - exactly once.

    Returns (updated_user_row, already_processed). already_processed is
    True when this exact payment had already been applied by an earlier
    call (duplicate verify request) - credits are NOT granted again in
    that case, callers just get the current state back.
    """
    if not (razorpay_order_id and razorpay_payment_id and razorpay_signature):
        raise PaymentServiceError("Missing payment verification fields.")

    payment_row = database.get_payment_by_order_id(razorpay_order_id)
    if not payment_row or payment_row["user_id"] != user["id"]:
        # Either the order doesn't exist, or it belongs to a different
        # account - never let a request apply someone else's order.
        raise PaymentServiceError("Payment order not found for this account.")

    if payment_row["status"] == "paid":
        return database.get_user(user["id"]), True

    client = _client()
    try:
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    except Exception as exc:  # noqa: BLE001 - any failure here means "not verified"
        database.mark_payment_failed(razorpay_order_id)
        raise PaymentServiceError(
            "Payment verification failed. No credits were added."
        ) from exc

    if payment_row["kind"] == "plan":
        plan = Config.PLANS[payment_row["plan"]]
        expires_at = database.add_months(time.time(), plan["validity_months"])
        applied, updated_user = database.apply_verified_plan_payment(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            user_id=user["id"],
            plan_id=payment_row["plan"],
            credits=plan["prompt_limit"],
            expires_at=expires_at,
        )
    else:
        applied, updated_user = database.apply_verified_topup_payment(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            user_id=user["id"],
            quantity=payment_row["topup_quantity"],
        )

    # applied=False here means a concurrent/duplicate request already won
    # the race and granted credits first - still not an error, just
    # nothing further to do.
    return updated_user, not applied
