from bson import ObjectId
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import re


def serialize_doc(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable dict."""
    if doc is None:
        return None
    result = {}
    for key, value in doc.items():
        if key == "_id":
            result["id"] = str(value)
        elif isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value
        elif isinstance(value, list):
            result[key] = [
                serialize_doc(item) if isinstance(item, dict) else
                str(item) if isinstance(item, ObjectId) else item
                for item in value
            ]
        elif isinstance(value, dict):
            result[key] = serialize_doc(value)
        else:
            result[key] = value
    return result


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_text(text: str) -> str:
    """Remove potentially dangerous characters from OCR output."""
    if not text:
        return ""
    # Remove null bytes and control characters
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Limit length
    return text[:5000]


def validate_object_id(id_str: str) -> ObjectId:
    """Validate and convert string to ObjectId."""
    try:
        return ObjectId(id_str)
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid ID format: {id_str}")


def build_pagination(page: int, page_size: int) -> tuple[int, int]:
    """Return (skip, limit) for MongoDB pagination."""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    return (page - 1) * page_size, page_size