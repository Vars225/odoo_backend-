from database import get_db
from utils.helpers import utcnow
from schemas import NotificationType
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)


async def create_notification(
    user_id: str,
    type: NotificationType,
    title: str,
    message: str,
    expense_id: str = None,
) -> dict:
    """Create an in-app notification for a user."""
    db = get_db()
    notif = {
        "user_id": user_id,
        "type": type.value,
        "title": title,
        "message": message,
        "expense_id": expense_id,
        "read": False,
        "created_at": utcnow(),
    }
    result = await db.notifications.insert_one(notif)
    notif["_id"] = result.inserted_id
    return notif


async def notify_expense_submitted(expense: dict, employee_name: str, manager_id: str):
    await create_notification(
        user_id=manager_id,
        type=NotificationType.approval_required,
        title="New Expense Awaiting Approval",
        message=f"{employee_name} submitted an expense of {expense['currency_original']} {expense['amount_original']:.2f} ({expense['category']}).",
        expense_id=str(expense["_id"]),
    )


async def notify_expense_decision(expense: dict, approved: bool, approver_name: str):
    status = "approved" if approved else "rejected"
    notif_type = NotificationType.expense_approved if approved else NotificationType.expense_rejected
    await create_notification(
        user_id=str(expense["user_id"]),
        type=notif_type,
        title=f"Expense {status.title()}",
        message=f"Your expense of {expense['currency_original']} {expense['amount_original']:.2f} was {status} by {approver_name}.",
        expense_id=str(expense["_id"]),
    )


async def notify_payment_processed(expense: dict):
    await create_notification(
        user_id=str(expense["user_id"]),
        type=NotificationType.payment_processed,
        title="Reimbursement Processed",
        message=f"Your expense reimbursement of {expense['company_currency']} {expense['amount_converted']:.2f} has been processed.",
        expense_id=str(expense["_id"]),
    )