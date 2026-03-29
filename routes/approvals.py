from fastapi import APIRouter, HTTPException, Depends, Query, status
from schemas import ApprovalActionRequest, CreateApprovalFlowRequest
from utils.auth import get_current_user, require_manager_or_admin, require_admin
from utils.helpers import utcnow, serialize_doc, validate_object_id
from services.workflow_service import process_approval_action, get_or_create_default_flow
from services.notification_service import notify_expense_decision
from database import get_db
from bson import ObjectId
from typing import List
import logging

router = APIRouter(prefix="/approvals", tags=["Approvals"])
logger = logging.getLogger(__name__)


@router.get("/pending")
async def get_pending_approvals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(require_manager_or_admin),
):
    """
    Manager/Admin: Get all expenses awaiting approval by the current user.
    """
    db = get_db()
    approver_id = str(current_user["_id"])

    pending = await db.approvals.find({
        "approver_id": approver_id,
        "status": "pending",
    }).sort("created_at", -1).to_list(1000)

    result = []
    for approval in pending:
        expense = await db.expenses.find_one({"_id": approval["expense_id"]})
        if not expense or expense["status"] not in ["in_review", "pending"]:
            continue

        submitter = await db.users.find_one({"_id": ObjectId(expense["user_id"])})
        exp_doc = serialize_doc(expense)
        exp_doc["user_name"] = submitter["name"] if submitter else "Unknown"
        exp_doc["user_department"] = submitter.get("department", "") if submitter else ""

        result.append({
            "approval_id": str(approval["_id"]),
            "step": approval["step"],
            "step_label": approval.get("step_label", f"Step {approval['step']}"),
            "expense": exp_doc,
            "created_at": approval["created_at"],
        })

    # Compute totals for dashboard
    total_queue_amount = sum(
        item["expense"]["amount_converted"] for item in result
    )

    return {
        "pending_approvals": result,
        "total": len(result),
        "total_queue_amount": round(total_queue_amount, 2),
        "page": page,
        "page_size": page_size,
    }


@router.post("/{expense_id}/approve")
async def approve_expense(
    expense_id: str,
    payload: ApprovalActionRequest = ApprovalActionRequest(),
    current_user: dict = Depends(require_manager_or_admin),
):
    """Manager/Admin: Approve an expense claim."""
    db = get_db()
    expense_oid = validate_object_id(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    if expense["status"] in ["approved", "rejected", "paid"]:
        raise HTTPException(status_code=400, detail=f"Expense already {expense['status']}")

    try:
        result = await process_approval_action(
            expense_id=expense_id,
            approver=current_user,
            approved=True,
            comment=payload.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Notify employee
    updated_expense = await db.expenses.find_one({"_id": expense_oid})
    is_fully_approved = updated_expense["status"] == "approved"
    if is_fully_approved:
        try:
            await notify_expense_decision(updated_expense, approved=True, approver_name=current_user["name"])
        except Exception as e:
            logger.warning(f"Notification error: {e}")

    return {
        "message": result.get("message", "Approval recorded"),
        "expense_status": result.get("status"),
        "next_approver_id": result.get("next_approver_id"),
        "fully_approved": is_fully_approved,
    }


@router.post("/{expense_id}/reject")
async def reject_expense(
    expense_id: str,
    payload: ApprovalActionRequest,
    current_user: dict = Depends(require_manager_or_admin),
):
    """Manager/Admin: Reject an expense claim (requires comment)."""
    if not payload.comment or len(payload.comment.strip()) < 5:
        raise HTTPException(
            status_code=400,
            detail="A rejection reason (comment) is required (min 5 chars)"
        )

    db = get_db()
    expense_oid = validate_object_id(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    if expense["status"] in ["approved", "rejected", "paid"]:
        raise HTTPException(status_code=400, detail=f"Expense already {expense['status']}")

    try:
        result = await process_approval_action(
            expense_id=expense_id,
            approver=current_user,
            approved=False,
            comment=payload.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Notify employee
    updated_expense = await db.expenses.find_one({"_id": expense_oid})
    try:
        await notify_expense_decision(updated_expense, approved=False, approver_name=current_user["name"])
    except Exception as e:
        logger.warning(f"Notification error: {e}")

    return {
        "message": "Expense rejected",
        "expense_status": "rejected",
    }


@router.get("/history")
async def get_approval_history(
    expense_id: str = Query(None),
    current_user: dict = Depends(require_manager_or_admin),
):
    """Get historical approvals (past approvals made by this user)."""
    db = get_db()
    query = {
        "approver_id": str(current_user["_id"]),
        "status": {"$in": ["approved", "rejected"]},
    }
    if expense_id:
        query["expense_id"] = ObjectId(expense_id)

    approvals = await db.approvals.find(query).sort("timestamp", -1).to_list(100)
    result = []
    for a in approvals:
        expense = await db.expenses.find_one({"_id": a["expense_id"]})
        doc = serialize_doc(a)
        if expense:
            doc["expense_amount"] = expense.get("amount_converted")
            doc["expense_currency"] = expense.get("company_currency")
            doc["expense_category"] = expense.get("category")
        result.append(doc)

    return {"history": result, "total": len(result)}


# ─── Approval Flow Configuration (Admin) ─────────────────────────────────────

@router.post("/flows", status_code=status.HTTP_201_CREATED)
async def create_approval_flow(
    payload: CreateApprovalFlowRequest,
    admin: dict = Depends(require_admin),
):
    """Admin: Define a custom approval workflow for the company."""
    db = get_db()
    now = utcnow()

    flow = {
        "company_id": admin["company_id"],
        "name": payload.name,
        "steps": [s.model_dump() for s in payload.steps],
        "rules": payload.rules.model_dump(),
        "applies_to_amounts_above": payload.applies_to_amounts_above,
        "created_at": now,
        "updated_at": now,
    }

    result = await db.approval_flows.insert_one(flow)
    flow["_id"] = result.inserted_id

    logger.info(f"Approval flow created: {payload.name}")
    return {"message": "Approval flow created", "flow": serialize_doc(flow)}


@router.get("/flows")
async def list_approval_flows(admin: dict = Depends(require_admin)):
    """Admin: List all approval flows for the company."""
    db = get_db()
    flows = await db.approval_flows.find({"company_id": admin["company_id"]}).to_list(50)
    return {"flows": [serialize_doc(f) for f in flows]}


@router.put("/flows/{flow_id}")
async def update_approval_flow(
    flow_id: str,
    payload: CreateApprovalFlowRequest,
    admin: dict = Depends(require_admin),
):
    """Admin: Update an existing approval flow."""
    db = get_db()
    flow_oid = validate_object_id(flow_id)

    update = {
        "name": payload.name,
        "steps": [s.model_dump() for s in payload.steps],
        "rules": payload.rules.model_dump(),
        "applies_to_amounts_above": payload.applies_to_amounts_above,
        "updated_at": utcnow(),
    }
    result = await db.approval_flows.update_one(
        {"_id": flow_oid, "company_id": admin["company_id"]},
        {"$set": update}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Approval flow not found")

    return {"message": "Approval flow updated"}