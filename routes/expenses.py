from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query, status
from schemas import CreateExpenseRequest, ExpenseCategory, ExpenseStatus
from utils.auth import get_current_user, require_any_role, require_manager_or_admin
from utils.helpers import utcnow, serialize_doc, validate_object_id, build_pagination
from utils.file_handler import save_upload
from services.currency_service import convert_currency
from services.workflow_service import initiate_workflow
from services.notification_service import notify_expense_submitted
from ocr.pipeline import process_receipt
from database import get_db
from bson import ObjectId
from datetime import datetime
from typing import Optional
import logging

router = APIRouter(prefix="/expenses", tags=["Expenses"])
logger = logging.getLogger(__name__)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def submit_expense(
    amount: float = Form(..., gt=0),
    currency: str = Form(..., min_length=3, max_length=3),
    category: ExpenseCategory = Form(...),
    description: str = Form(..., min_length=5),
    date: datetime = Form(...),
    merchant_name: Optional[str] = Form(None),
    project_code: Optional[str] = Form(None),
    is_manager_approver: bool = Form(False),
    receipt: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Employee: Submit a new expense claim.
    Supports multi-currency (auto-converts to company base currency).
    Optionally upload a receipt image.
    """
    db = get_db()
    currency = currency.upper()

    # Fetch company for base currency
    company = await db.companies.find_one({"_id": ObjectId(current_user["company_id"])})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    base_currency = company["base_currency"]

    # Convert currency
    try:
        amount_converted = await convert_currency(amount, currency, base_currency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Handle receipt upload
    receipt_url = None
    ocr_data = None
    if receipt and receipt.filename:
        try:
            relative_path, file_bytes = await save_upload(receipt, subdir="receipts")
            receipt_url = f"/files/{relative_path}"

            # Run OCR pipeline
            ocr_result = await process_receipt(file_bytes)
            if ocr_result.parsed_successfully:
                ocr_data = ocr_result.model_dump()
                logger.info(f"OCR extracted: merchant={ocr_result.merchant_name}, amount={ocr_result.amount}")
        except Exception as e:
            logger.warning(f"Receipt processing failed: {e}")

    now = utcnow()
    expense = {
        "user_id": str(current_user["_id"]),
        "company_id": current_user["company_id"],
        "amount_original": amount,
        "currency_original": currency,
        "amount_converted": amount_converted,
        "company_currency": base_currency,
        "category": category.value,
        "description": description,
        "merchant_name": merchant_name or (ocr_data.get("merchant_name") if ocr_data else None),
        "project_code": project_code,
        "receipt_url": receipt_url,
        "status": ExpenseStatus.pending.value,
        "is_manager_approver": is_manager_approver,
        "current_step": 0,
        "flow_id": None,
        "ocr_data": ocr_data,
        "date": date,
        "created_at": now,
        "updated_at": now,
    }

    result = await db.expenses.insert_one(expense)
    expense["_id"] = result.inserted_id
    expense_id = str(result.inserted_id)

    # Initiate approval workflow
    try:
        await initiate_workflow(expense_id, current_user["company_id"], current_user)
    except Exception as e:
        logger.error(f"Workflow initiation failed: {e}")

    # Notify manager
    try:
        if current_user.get("manager_id"):
            await notify_expense_submitted(expense, current_user["name"], current_user["manager_id"])
    except Exception as e:
        logger.warning(f"Notification failed: {e}")

    return {
        "message": "Expense submitted successfully",
        "expense": serialize_doc(expense),
        "ocr_extracted": ocr_data is not None,
    }


@router.get("/")
async def list_expenses(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    List expenses.
    - Employees: see only their own
    - Managers: see their team's expenses
    - Admins: see all company expenses
    """
    db = get_db()
    role = current_user["role"]
    query = {"company_id": current_user["company_id"]}

    if role == "employee":
        query["user_id"] = str(current_user["_id"])
    elif role == "manager":
        # Manager sees their own + their direct reports
        team_ids = await _get_team_ids(str(current_user["_id"]), db)
        query["user_id"] = {"$in": team_ids}

    if status:
        query["status"] = status
    if category:
        query["category"] = category

    skip, limit = build_pagination(page, page_size)
    total = await db.expenses.count_documents(query)
    expenses = await db.expenses.find(query).sort("created_at", -1).skip(skip).limit(limit).to_list(limit)

    # Enrich with user names
    enriched = []
    for exp in expenses:
        doc = serialize_doc(exp)
        user = await db.users.find_one({"_id": ObjectId(exp["user_id"])})
        doc["user_name"] = user["name"] if user else "Unknown"
        enriched.append(doc)

    return {
        "expenses": enriched,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/summary")
async def expense_summary(current_user: dict = Depends(get_current_user)):
    """Dashboard summary: total, pending count, approved amount, status breakdown."""
    db = get_db()
    role = current_user["role"]
    match_query = {"company_id": current_user["company_id"]}

    if role == "employee":
        match_query["user_id"] = str(current_user["_id"])
    elif role == "manager":
        team_ids = await _get_team_ids(str(current_user["_id"]), db)
        match_query["user_id"] = {"$in": team_ids}

    pipeline = [
        {"$match": match_query},
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total_amount": {"$sum": "$amount_converted"},
        }}
    ]
    results = await db.expenses.aggregate(pipeline).to_list(20)

    summary = {"total_expenses": 0, "total_amount": 0.0}
    for r in results:
        s = r["_id"]
        summary[s] = {"count": r["count"], "amount": r["total_amount"]}
        summary["total_expenses"] += r["count"]
        summary["total_amount"] += r["total_amount"]

    return summary


@router.get("/{expense_id}")
async def get_expense(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get a single expense with approval timeline."""
    db = get_db()
    expense_oid = validate_object_id(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid, "company_id": current_user["company_id"]})

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    # Check access
    role = current_user["role"]
    user_id = str(current_user["_id"])
    if role == "employee" and expense["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    doc = serialize_doc(expense)

    # Fetch submitter info
    submitter = await db.users.find_one({"_id": ObjectId(expense["user_id"])})
    doc["user_name"] = submitter["name"] if submitter else "Unknown"
    doc["user_email"] = submitter["email"] if submitter else ""

    # Fetch approval timeline
    approvals = await db.approvals.find(
        {"expense_id": expense_oid}
    ).sort("step", 1).to_list(50)

    timeline = []
    for a in approvals:
        approver = await db.users.find_one({"_id": ObjectId(a["approver_id"])}) if a.get("approver_id") else None
        item = serialize_doc(a)
        item["approver_name"] = approver["name"] if approver else "Unknown"
        item["approver_email"] = approver["email"] if approver else ""
        timeline.append(item)

    doc["approval_timeline"] = timeline

    return doc


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(
    expense_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Employee: Delete a pending expense (only if not yet reviewed)."""
    db = get_db()
    expense_oid = validate_object_id(expense_id)
    expense = await db.expenses.find_one({"_id": expense_oid})

    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    if expense["user_id"] != str(current_user["_id"]) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    if expense["status"] not in [ExpenseStatus.pending.value]:
        raise HTTPException(status_code=400, detail="Can only delete pending expenses")

    await db.expenses.delete_one({"_id": expense_oid})
    await db.approvals.delete_many({"expense_id": expense_oid})


async def _get_team_ids(manager_id: str, db) -> list:
    """Return list of user IDs that report to this manager."""
    team = await db.users.find({"manager_id": manager_id}).to_list(500)
    ids = [str(u["_id"]) for u in team]
    ids.append(manager_id)  # Include manager's own expenses
    return ids