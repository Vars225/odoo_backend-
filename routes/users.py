from fastapi import APIRouter, HTTPException, Depends, status
from schemas import CreateUserRequest, UpdateUserRequest, UserResponse
from utils.auth import get_current_user, require_admin, require_any_role
from utils.auth import hash_password
from utils.helpers import utcnow, serialize_doc, validate_object_id
from database import get_db
from bson import ObjectId
from typing import List
import logging

router = APIRouter(prefix="/users", tags=["User Management"])
logger = logging.getLogger(__name__)


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    admin: dict = Depends(require_admin)
):
    """Admin: Create a new employee or manager in the same company."""
    db = get_db()
    company_id = admin["company_id"]

    existing = await db.users.find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=409, detail="Email already in use")

    # Validate manager_id if provided
    if payload.manager_id:
        mgr_oid = validate_object_id(payload.manager_id)
        manager = await db.users.find_one({
            "_id": mgr_oid,
            "company_id": company_id,
            "role": {"$in": ["manager", "admin"]}
        })
        if not manager:
            raise HTTPException(status_code=404, detail="Manager not found in company")

    now = utcnow()
    user = {
        "name": payload.name,
        "email": payload.email.lower(),
        "password_hash": hash_password(payload.password),
        "role": payload.role.value,
        "company_id": company_id,
        "manager_id": payload.manager_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.users.insert_one(user)
    user["_id"] = result.inserted_id
    doc = serialize_doc(user)
    doc.pop("password_hash", None)

    logger.info(f"User created: {payload.email} role={payload.role.value}")
    return {"message": "User created successfully", "user": doc}


@router.get("/", response_model=List[dict])
async def list_users(
    role: str = None,
    current_user: dict = Depends(require_admin)
):
    """Admin: List all users in the company."""
    db = get_db()
    query = {"company_id": current_user["company_id"]}
    if role:
        query["role"] = role

    users = await db.users.find(query).to_list(500)
    result = []
    for u in users:
        doc = serialize_doc(u)
        doc.pop("password_hash", None)
        result.append(doc)
    return result


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    current_user: dict = Depends(require_any_role)
):
    """Get user by ID (admins see all, others see only themselves)."""
    db = get_db()

    # Non-admins can only view their own profile
    if current_user["role"] != "admin" and str(current_user["_id"]) != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    user_oid = validate_object_id(user_id)
    user = await db.users.find_one({
        "_id": user_oid,
        "company_id": current_user["company_id"]
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    doc = serialize_doc(user)
    doc.pop("password_hash", None)
    return doc


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    admin: dict = Depends(require_admin)
):
    """Admin: Update user's name, role, or manager assignment."""
    db = get_db()
    user_oid = validate_object_id(user_id)

    update_fields = {"updated_at": utcnow()}
    if payload.name is not None:
        update_fields["name"] = payload.name
    if payload.role is not None:
        update_fields["role"] = payload.role.value
    if payload.manager_id is not None:
        # Validate manager
        mgr_oid = validate_object_id(payload.manager_id)
        manager = await db.users.find_one({
            "_id": mgr_oid,
            "company_id": admin["company_id"],
            "role": {"$in": ["manager", "admin"]}
        })
        if not manager:
            raise HTTPException(status_code=404, detail="Manager not found")
        update_fields["manager_id"] = payload.manager_id

    result = await db.users.update_one(
        {"_id": user_oid, "company_id": admin["company_id"]},
        {"$set": update_fields}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")

    return {"message": "User updated successfully"}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    admin: dict = Depends(require_admin)
):
    """Admin: Remove a user from the company."""
    db = get_db()
    user_oid = validate_object_id(user_id)

    if user_id == str(admin["_id"]):
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    result = await db.users.delete_one({
        "_id": user_oid,
        "company_id": admin["company_id"]
    })

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")


@router.get("/managers/list")
async def list_managers(current_user: dict = Depends(require_any_role)):
    """List all managers in the company (used for dropdown assignment)."""
    db = get_db()
    managers = await db.users.find(
        {"company_id": current_user["company_id"], "role": {"$in": ["manager", "admin"]}}
    ).to_list(200)
    return [{"id": str(m["_id"]), "name": m["name"], "role": m["role"]} for m in managers]