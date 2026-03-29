from fastapi import APIRouter, HTTPException, Depends, status
from schemas import SignupRequest, LoginRequest, TokenResponse
from utils.auth import hash_password, verify_password, create_access_token, get_current_user
from utils.helpers import utcnow, serialize_doc
from services.currency_service import get_country_currency
from database import get_db
import logging

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest):
    """
    Register a new admin user and auto-create their company.
    Auto-detects the country's base currency.
    """
    db = get_db()

    # Check email uniqueness
    existing = await db.users.find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Resolve currency from country
    base_currency = await get_country_currency(payload.country)
    if not base_currency:
        base_currency = "USD"
        logger.warning(f"Could not resolve currency for country '{payload.country}', defaulting to USD")

    # Create company
    now = utcnow()
    company = {
        "name": payload.company_name,
        "country": payload.country,
        "base_currency": base_currency.upper(),
        "created_at": now,
        "updated_at": now,
    }
    company_result = await db.companies.insert_one(company)
    company_id = str(company_result.inserted_id)

    # Create admin user
    user = {
        "name": payload.name,
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "role": "admin",
        "company_id": company_id,
        "manager_id": None,
        "created_at": now,
        "updated_at": now,
    }
    user_result = await db.users.insert_one(user)
    user["_id"] = user_result.inserted_id
    user_id = str(user_result.inserted_id)

    # Generate token
    token = create_access_token({"sub": user_id, "role": "admin", "company_id": company_id})

    logger.info(f"✅ New company registered: {payload.company_name} ({base_currency})")

    return TokenResponse(
        access_token=token,
        user={
            "id": user_id,
            "name": payload.name,
            "email": payload.email,
            "role": "admin",
            "company_id": company_id,
            "company_name": payload.company_name,
            "base_currency": base_currency,
        }
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest):
    """Authenticate user and return JWT token."""
    db = get_db()

    user = await db.users.find_one({"email": payload.email.lower()})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # Fetch company info
    from bson import ObjectId
    company = await db.companies.find_one({"_id": ObjectId(user["company_id"])})

    user_id = str(user["_id"])
    token = create_access_token({
        "sub": user_id,
        "role": user["role"],
        "company_id": user["company_id"]
    })

    return TokenResponse(
        access_token=token,
        user={
            "id": user_id,
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
            "company_id": user["company_id"],
            "company_name": company["name"] if company else "",
            "base_currency": company["base_currency"] if company else "USD",
        }
    )


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user's profile."""
    from bson import ObjectId
    db = get_db()
    company = await db.companies.find_one({"_id": ObjectId(current_user["company_id"])})
    return {
        "id": str(current_user["_id"]),
        "name": current_user["name"],
        "email": current_user["email"],
        "role": current_user["role"],
        "company_id": current_user["company_id"],
        "company": serialize_doc(company) if company else None,
        "manager_id": current_user.get("manager_id"),
    }