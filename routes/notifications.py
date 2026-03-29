from fastapi import APIRouter, Depends, Query
from utils.auth import get_current_user
from utils.helpers import serialize_doc, utcnow, validate_object_id
from database import get_db
import logging

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = logging.getLogger(__name__)


@router.get("/")
async def get_notifications(
    unread_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Get notifications for the current user."""
    db = get_db()
    query = {"user_id": str(current_user["_id"])}
    if unread_only:
        query["read"] = False

    skip = (page - 1) * page_size
    total = await db.notifications.count_documents(query)
    notifs = await db.notifications.find(query).sort("created_at", -1).skip(skip).limit(page_size).to_list(page_size)

    return {
        "notifications": [serialize_doc(n) for n in notifs],
        "total": total,
        "unread_count": await db.notifications.count_documents({"user_id": str(current_user["_id"]), "read": False}),
    }


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Mark a notification as read."""
    db = get_db()
    notif_oid = validate_object_id(notification_id)
    await db.notifications.update_one(
        {"_id": notif_oid, "user_id": str(current_user["_id"])},
        {"$set": {"read": True}}
    )
    return {"message": "Marked as read"}


@router.patch("/read-all")
async def mark_all_read(current_user: dict = Depends(get_current_user)):
    """Mark all notifications as read."""
    db = get_db()
    result = await db.notifications.update_many(
        {"user_id": str(current_user["_id"]), "read": False},
        {"$set": {"read": True}}
    )
    return {"message": f"Marked {result.modified_count} notifications as read"}