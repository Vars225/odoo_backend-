from fastapi import APIRouter, Depends, HTTPException
from utils.auth import get_current_user, require_admin
from utils.helpers import serialize_doc, utcnow, validate_object_id
from services.currency_service import get_exchange_rates
from database import get_db
from bson import ObjectId
import logging

router = APIRouter(prefix="/company", tags=["Company"])
logger = logging.getLogger(__name__)


@router.get("/")
async def get_company(current_user: dict = Depends(get_current_user)):
    """Get current user's company info."""
    db = get_db()
    company = await db.companies.find_one({"_id": ObjectId(current_user["company_id"])})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return serialize_doc(company)


@router.patch("/currency")
async def update_base_currency(
    base_currency: str,
    admin: dict = Depends(require_admin),
):
    """Admin: Update company's base currency."""
    db = get_db()
    base_currency = base_currency.upper().strip()
    if len(base_currency) != 3:
        raise HTTPException(status_code=400, detail="Currency must be a 3-letter ISO code (e.g. USD)")

    # Validate currency exists
    try:
        rates = await get_exchange_rates(base_currency)
        if not rates:
            raise ValueError("Invalid currency")
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid or unsupported currency: {base_currency}")

    await db.companies.update_one(
        {"_id": ObjectId(admin["company_id"])},
        {"$set": {"base_currency": base_currency, "updated_at": utcnow()}}
    )
    return {"message": f"Base currency updated to {base_currency}"}


@router.get("/currencies")
async def get_supported_currencies(current_user: dict = Depends(get_current_user)):
    """Get list of supported currencies (from exchange rate API)."""
    db = get_db()
    company = await db.companies.find_one({"_id": ObjectId(current_user["company_id"])})
    base = company["base_currency"] if company else "USD"

    rates = await get_exchange_rates(base)
    currencies = sorted(rates.keys())
    return {
        "base_currency": base,
        "supported_currencies": currencies,
        "total": len(currencies),
    }


@router.get("/stats")
async def company_stats(admin: dict = Depends(require_admin)):
    """Admin dashboard: company-wide expense statistics."""
    db = get_db()
    company_id = admin["company_id"]

    user_count = await db.users.count_documents({"company_id": company_id})
    expense_pipeline = [
        {"$match": {"company_id": company_id}},
        {"$group": {
            "_id": "$status",
            "count": {"$sum": 1},
            "total": {"$sum": "$amount_converted"},
        }}
    ]
    expense_stats = await db.expenses.aggregate(expense_pipeline).to_list(20)

    stats = {
        "company_id": company_id,
        "total_users": user_count,
        "expenses_by_status": {},
        "total_reimbursed": 0.0,
    }
    for s in expense_stats:
        stats["expenses_by_status"][s["_id"]] = {
            "count": s["count"],
            "total": round(s["total"], 2),
        }
        if s["_id"] == "paid":
            stats["total_reimbursed"] = round(s["total"], 2)

    return stats