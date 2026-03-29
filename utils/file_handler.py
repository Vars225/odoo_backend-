import aiofiles
import os
import uuid
import logging
from pathlib import Path
from fastapi import HTTPException, UploadFile
from config import settings

logger = logging.getLogger(__name__)

UPLOAD_BASE = Path(settings.upload_dir)


def get_upload_path(subdir: str = "receipts") -> Path:
    path = UPLOAD_BASE / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_upload(file: UploadFile) -> None:
    """Validate file type and size constraints."""
    if file.content_type not in settings.allowed_file_types_list:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file.content_type}' not allowed. Allowed: {settings.allowed_file_types}"
        )


async def save_upload(file: UploadFile, subdir: str = "receipts") -> tuple[str, bytes]:
    """Save uploaded file and return (relative_path, file_bytes)."""
    validate_upload(file)

    # Read content
    content = await file.read()

    if len(content) > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Max size: {settings.max_file_size_mb}MB"
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded")

    # Generate safe filename
    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    upload_path = get_upload_path(subdir)
    file_path = upload_path / filename

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    relative_path = f"{subdir}/{filename}"
    logger.info(f"File saved: {relative_path}")
    return relative_path, content


def delete_file(relative_path: str) -> None:
    """Delete an uploaded file."""
    file_path = UPLOAD_BASE / relative_path
    if file_path.exists():
        file_path.unlink()
        logger.info(f"Deleted file: {relative_path}")