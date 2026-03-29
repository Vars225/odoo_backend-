import stripe
import logging
from config import settings
from database import get_db
from utils.helpers import utcnow, serialize_doc
from schemas import PaymentStatus, ExpenseStatus
from bson import ObjectId

logger = logging.getLogger(__name__)

stripe.api_key = settings.stripe_secret_key


async def create_payment_intent(expense_id: str) -> dict:
    """
    Create a Stripe PaymentIntent for a fully approved expense.
    Returns the payment record with client_secret.
    """
    db = get_db()
    expense_oid = ObjectId(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise ValueError("Expense not found")

    if expense["status"] != ExpenseStatus.approved.value:
        raise ValueError("Expense must be fully approved before payment")

    # Check if payment already exists
    existing = await db.payments.find_one({"expense_id": expense_oid})
    if existing and existing["status"] == PaymentStatus.succeeded.value:
        raise ValueError("Payment already processed for this expense")

    amount_cents = int(expense["amount_converted"] * 100)  # Stripe uses smallest currency unit
    currency = expense["company_currency"].lower()

    # Create Stripe PaymentIntent
    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            metadata={
                "expense_id": expense_id,
                "company_id": str(expense["company_id"]),
                "user_id": str(expense["user_id"]),
                "category": expense.get("category", ""),
            },
            description=f"Expense reimbursement: {expense.get('description', '')[:200]}",
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {e}")
        raise ValueError(f"Payment creation failed: {str(e)}")

    # Store payment record
    payment = {
        "expense_id": expense_oid,
        "stripe_payment_id": intent.id,
        "client_secret": intent.client_secret,
        "status": PaymentStatus.processing.value,
        "amount": expense["amount_converted"],
        "currency": expense["company_currency"],
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    await db.payments.replace_one(
        {"expense_id": expense_oid},
        payment,
        upsert=True
    )

    # Update expense status
    await db.expenses.update_one(
        {"_id": expense_oid},
        {"$set": {"status": ExpenseStatus.paid.value, "updated_at": utcnow()}}
    )

    logger.info(f"✅ PaymentIntent created: {intent.id} for expense {expense_id}")
    return serialize_doc(payment)


async def confirm_payment(stripe_payment_id: str) -> dict:
    """
    Handle Stripe webhook or manual confirmation.
    Updates payment status to succeeded/failed.
    """
    db = get_db()

    try:
        intent = stripe.PaymentIntent.retrieve(stripe_payment_id)
        stripe_status = intent.status  # "succeeded", "canceled", "requires_payment_method"
    except stripe.error.StripeError as e:
        raise ValueError(f"Failed to retrieve payment: {e}")

    status_map = {
        "succeeded": PaymentStatus.succeeded.value,
        "canceled": PaymentStatus.failed.value,
        "requires_payment_method": PaymentStatus.failed.value,
    }
    payment_status = status_map.get(stripe_status, PaymentStatus.processing.value)

    result = await db.payments.find_one_and_update(
        {"stripe_payment_id": stripe_payment_id},
        {"$set": {"status": payment_status, "updated_at": utcnow()}},
        return_document=True
    )

    if result and payment_status == PaymentStatus.succeeded.value:
        # Ensure expense is marked paid
        await db.expenses.update_one(
            {"_id": result["expense_id"]},
            {"$set": {"status": ExpenseStatus.paid.value, "updated_at": utcnow()}}
        )

    return serialize_doc(result) if result else {}


async def handle_stripe_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process Stripe webhook events."""
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise ValueError("Invalid Stripe webhook signature")

    if event["type"] == "payment_intent.succeeded":
        payment_intent = event["data"]["object"]
        return await confirm_payment(payment_intent["id"])

    if event["type"] == "payment_intent.payment_failed":
        payment_intent = event["data"]["object"]
        db = get_db()
        await db.payments.update_one(
            {"stripe_payment_id": payment_intent["id"]},
            {"$set": {"status": PaymentStatus.failed.value, "updated_at": utcnow()}}
        )

    return {"received": True}