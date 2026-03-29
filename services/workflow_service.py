"""
Approval Workflow Engine
Handles sequential, percentage-based, specific-approver, and hybrid approval flows.
"""
from database import get_db
from utils.helpers import utcnow, serialize_doc
from schemas import ApprovalRuleType, ExpenseStatus, ApprovalStatus
from bson import ObjectId
from typing import Optional
import logging

logger = logging.getLogger(__name__)


async def get_or_create_default_flow(company_id: str) -> dict:
    """Return the company's approval flow, creating a default one if none exists."""
    db = get_db()
    flow = await db.approval_flows.find_one({"company_id": company_id})
    if not flow:
        # Default: sequential Manager → Finance → Director
        default_flow = {
            "company_id": company_id,
            "name": "Default Approval Flow",
            "steps": [
                {"step": 1, "role": "manager", "approver_id": None, "label": "Manager Review"},
                {"step": 2, "role": "finance", "approver_id": None, "label": "Finance Approval"},
                {"step": 3, "role": "director", "approver_id": None, "label": "Director Approval"},
            ],
            "rules": {
                "type": ApprovalRuleType.sequential.value,
                "percentage": None,
                "special_approver_id": None,
                "description": "All approvers must approve in sequence",
            },
            "applies_to_amounts_above": None,
            "created_at": utcnow(),
        }
        result = await db.approval_flows.insert_one(default_flow)
        default_flow["_id"] = result.inserted_id
        return default_flow
    return flow


async def initiate_workflow(expense_id: str, company_id: str, user: dict) -> dict:
    """
    Start the approval workflow for a newly submitted expense.
    Returns the first approval record created.
    """
    db = get_db()
    expense_oid = ObjectId(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise ValueError(f"Expense {expense_id} not found")

    flow = await get_or_create_default_flow(company_id)
    steps = sorted(flow["steps"], key=lambda s: s["step"])

    if not steps:
        # No steps: auto-approve
        await db.expenses.update_one(
            {"_id": expense_oid},
            {"$set": {"status": ExpenseStatus.approved.value, "updated_at": utcnow()}}
        )
        return {"message": "Auto-approved (no workflow steps)"}

    # Manager-first logic: if is_manager_approver is True, find the employee's manager
    first_step = steps[0]
    approver_id = await _resolve_approver(first_step, user, db)

    # Create the first approval record
    approval = {
        "expense_id": expense_oid,
        "approver_id": approver_id,
        "step": first_step["step"],
        "step_label": first_step.get("label", f"Step {first_step['step']}"),
        "flow_id": str(flow["_id"]),
        "status": ApprovalStatus.pending.value,
        "comment": None,
        "timestamp": None,
        "created_at": utcnow(),
    }
    result = await db.approvals.insert_one(approval)
    approval["_id"] = result.inserted_id

    # Update expense status
    await db.expenses.update_one(
        {"_id": expense_oid},
        {"$set": {
            "status": ExpenseStatus.in_review.value,
            "current_step": first_step["step"],
            "flow_id": str(flow["_id"]),
            "updated_at": utcnow(),
        }}
    )

    return serialize_doc(approval)


async def _resolve_approver(step: dict, submitter: dict, db) -> str:
    """Determine who should receive the approval for this step."""
    # Specific approver override
    if step.get("approver_id"):
        return step["approver_id"]

    # Manager role: use submitter's direct manager
    role = step.get("role", "").lower()
    if role == "manager":
        manager_id = submitter.get("manager_id")
        if manager_id:
            return str(manager_id)
        # Fallback to first manager/admin in company
        mgr = await db.users.find_one({
            "company_id": submitter["company_id"],
            "role": {"$in": ["manager", "admin"]}
        })
        if mgr:
            return str(mgr["_id"])

    # Finance / Director / other roles: find first user with that role
    user = await db.users.find_one({
        "company_id": submitter["company_id"],
        "role": role
    })
    if user:
        return str(user["_id"])

    # Fallback to admin
    admin = await db.users.find_one({"company_id": submitter["company_id"], "role": "admin"})
    return str(admin["_id"]) if admin else str(submitter["_id"])


async def process_approval_action(
    expense_id: str,
    approver: dict,
    approved: bool,
    comment: Optional[str]
) -> dict:
    """
    Process approve/reject action.
    - If approved: check if workflow complete or advance to next step
    - If rejected: mark expense rejected immediately
    Returns updated expense status info.
    """
    db = get_db()
    expense_oid = ObjectId(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise ValueError("Expense not found")

    approver_oid = approver["_id"]
    approver_id_str = str(approver_oid)

    # Find the pending approval for this approver + expense
    pending_approval = await db.approvals.find_one({
        "expense_id": expense_oid,
        "approver_id": approver_id_str,
        "status": ApprovalStatus.pending.value,
    })

    if not pending_approval:
        raise ValueError("No pending approval found for this approver on this expense")

    current_step = pending_approval["step"]
    action_status = ApprovalStatus.approved.value if approved else ApprovalStatus.rejected.value

    # Update approval record
    await db.approvals.update_one(
        {"_id": pending_approval["_id"]},
        {"$set": {
            "status": action_status,
            "comment": comment,
            "timestamp": utcnow(),
        }}
    )

    if not approved:
        # Reject: terminate workflow
        await db.expenses.update_one(
            {"_id": expense_oid},
            {"$set": {"status": ExpenseStatus.rejected.value, "updated_at": utcnow()}}
        )
        return {"status": ExpenseStatus.rejected.value, "message": "Expense rejected"}

    # Approved: check conditional rules
    flow = await db.approval_flows.find_one({"_id": ObjectId(expense.get("flow_id", ""))})
    if not flow:
        flow = await get_or_create_default_flow(str(expense["company_id"]))

    rules = flow.get("rules", {})
    rule_type = rules.get("type", ApprovalRuleType.sequential.value)

    # Check if special approver rule triggers auto-approval
    if rule_type in [ApprovalRuleType.specific_approver.value, ApprovalRuleType.hybrid.value]:
        special_id = rules.get("special_approver_id")
        if special_id and approver_id_str == special_id:
            await _finalize_approval(expense_oid, db)
            return {"status": ExpenseStatus.approved.value, "message": "Auto-approved by special approver"}

    # Check percentage rule
    if rule_type in [ApprovalRuleType.percentage.value, ApprovalRuleType.hybrid.value]:
        pct_threshold = rules.get("percentage", 100)
        if pct_threshold:
            total_steps = len(flow["steps"])
            approved_count = await db.approvals.count_documents({
                "expense_id": expense_oid,
                "status": ApprovalStatus.approved.value
            })
            pct_approved = (approved_count / total_steps) * 100
            if pct_approved >= pct_threshold:
                await _finalize_approval(expense_oid, db)
                return {"status": ExpenseStatus.approved.value, "message": f"Approved via {pct_approved:.0f}% rule"}

    # Sequential: advance to next step
    steps = sorted(flow["steps"], key=lambda s: s["step"])
    next_steps = [s for s in steps if s["step"] > current_step]

    if not next_steps:
        # All steps done → approved
        await _finalize_approval(expense_oid, db)
        return {"status": ExpenseStatus.approved.value, "message": "All approvals complete"}

    # Create next approval record
    next_step = next_steps[0]
    submitter = await db.users.find_one({"_id": ObjectId(expense["user_id"])})
    next_approver_id = await _resolve_approver(next_step, submitter or {}, db)

    next_approval = {
        "expense_id": expense_oid,
        "approver_id": next_approver_id,
        "step": next_step["step"],
        "step_label": next_step.get("label", f"Step {next_step['step']}"),
        "flow_id": str(flow["_id"]),
        "status": ApprovalStatus.pending.value,
        "comment": None,
        "timestamp": None,
        "created_at": utcnow(),
    }
    await db.approvals.insert_one(next_approval)

    await db.expenses.update_one(
        {"_id": expense_oid},
        {"$set": {"current_step": next_step["step"], "updated_at": utcnow()}}
    )

    return {
        "status": ExpenseStatus.in_review.value,
        "message": f"Advanced to step {next_step['step']}: {next_step.get('label', '')}",
        "next_approver_id": next_approver_id,
    }


async def _finalize_approval(expense_oid: ObjectId, db):
    """Mark expense as fully approved."""
    await db.expenses.update_one(
        {"_id": expense_oid},
        {"$set": {"status": ExpenseStatus.approved.value, "updated_at": utcnow()}}
    )