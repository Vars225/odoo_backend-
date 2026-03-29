from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, status
from ocr.pipeline import process_receipt
from utils.auth import get_current_user
from utils.file_handler import save_upload
import logging

router = APIRouter(prefix="/ocr", tags=["OCR"])
logger = logging.getLogger(__name__)


@router.post("/scan")
async def scan_receipt(
    receipt: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload a receipt image and extract structured expense data via OCR.
    Use this to auto-populate the expense submission form.
    """
    if not receipt.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content_type = receipt.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="Only image files supported for OCR (JPEG, PNG, WEBP)"
        )

    try:
        _, file_bytes = await save_upload(receipt, subdir="ocr_temp")
        result = await process_receipt(file_bytes)
    except Exception as e:
        logger.error(f"OCR scan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"OCR processing failed: {str(e)}")

    return {
        "success": result.parsed_successfully,
        "confidence": result.confidence,
        "extracted": {
            "merchant_name": result.merchant_name,
            "amount": result.amount,
            "currency": result.currency,
            "date": result.date,
            "description": result.description,
            "expense_lines": [line.model_dump() for line in result.expense_lines],
        },
        "raw_text": result.raw_text[:1000] if result.raw_text else "",
    }