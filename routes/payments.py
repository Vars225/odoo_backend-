from fastapi import APIRouter, HTTPException, Depends, Request, Header, status
from schemas import ConfirmPaymentRequest
from utils.auth import get_current_user, require_admin, require_manager_or_admin
from utils.helpers import serialize_doc, validate_object_id
from services.stripe_service import create_payment_intent, confirm_payment, handle_stripe_webhook
from services.notification_service import notify_payment_processed
from database import get_db
from bson import ObjectId
import logging

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger(__name__)


@router.post("/initiate")
async def initiate_payment(
    payload: ConfirmPaymentRequest,
    current_user: dict = Depends(require_manager_or_admin),
):
    """
    Admin/Manager: Trigger Stripe PaymentIntent for a fully approved expense.
    Returns client_secret for frontend to confirm.
    """
    db = get_db()
    expense_oid = validate_object_id(payload.expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid, "company_id": current_user["company_id"]})

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    if expense["status"] != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Expense must be fully approved. Current status: {expense['status']}"
        )

    try:
        payment = await create_payment_intent(payload.expense_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Notify employee
    try:
        await notify_payment_processed(expense)
    except Exception as e:
        logger.warning(f"Payment notification failed: {e}")

    return {
        "message": "Payment initiated successfully",
        "payment": payment,
        "expense_status": "paid",
    }


@router.post("/confirm")
async def confirm_payment_route(
    stripe_payment_id: str,
    current_user: dict = Depends(require_manager_or_admin),
):
    """Manually confirm a Stripe payment and sync its status."""
    try:
        result = await confirm_payment(stripe_payment_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"message": "Payment status synced", "payment": result}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(None, alias="stripe-signature"),
):
    """Stripe webhook endpoint for async payment event handling."""
    payload = await request.body()
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Missing Stripe signature")

    try:
        result = await handle_stripe_webhook(payload, stripe_signature)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result


@router.get("/{expense_id}")
async def get_payment_status(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get payment record for an expense."""
    db = get_db()
    expense_oid = validate_object_id(expense_id)

    # Verify access
    expense = await db.expenses.find_one({"_id": expense_oid, "company_id": current_user["company_id"]})
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    if current_user["role"] == "employee" and expense["user_id"] != str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="Access denied")

    payment = await db.payments.find_one({"expense_id": expense_oid})
    if not payment:
        raise HTTPException(status_code=404, detail="No payment record found")

    doc = serialize_doc(payment)
    doc.pop("client_secret", None)  # Never expose client_secret to employees
    return doc