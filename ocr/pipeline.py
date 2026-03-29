import cv2
import numpy as np
import pytesseract
import re
import logging
from pathlib import Path
from typing import Optional, List, Tuple
from PIL import Image
import io
from schemas import OCRResult, OCRExpenseLine
from utils.helpers import sanitize_text

logger = logging.getLogger(__name__)

# Common merchant name patterns
MERCHANT_PATTERNS = [
    r'^([A-Z][A-Za-z\s&\'.,-]+(?:LLC|Inc|Ltd|Co|Corp|Hotel|Airlines|Airways|Bistro|Restaurant|Cafe|Coffee|Store|Shop|Services|Technologies)?)',
    r'(?:MERCHANT|STORE|VENDOR|FROM|SELLER)[\s:]+([A-Z][A-Za-z\s&]+)',
]

# Amount patterns
AMOUNT_PATTERNS = [
    r'(?:TOTAL|AMOUNT|GRAND TOTAL|SUBTOTAL|DUE|BALANCE)[\s:$]*([0-9,]+\.?\d*)',
    r'\$\s*([0-9,]+\.\d{2})',
    r'([0-9,]+\.\d{2})\s*(?:USD|EUR|GBP|INR|CAD|AUD)',
    r'(?:Rs\.?|₹)\s*([0-9,]+\.?\d*)',
]

# Date patterns
DATE_PATTERNS = [
    r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})',
    r'(\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})',
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}',
    r'\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}',
]

# Currency symbols → codes
CURRENCY_SYMBOLS = {
    '$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR',
    '¥': 'JPY', 'A$': 'AUD', 'C$': 'CAD', 'S$': 'SGD',
}


def preprocess_image(image_bytes: bytes) -> np.ndarray:
    """Apply OpenCV preprocessing for better OCR accuracy."""
    # Convert bytes to numpy array
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Failed to decode image")

    # 1. Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 2. Upscale for better OCR (2x)
    scale = 2.0
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # 3. Deskew (straighten rotated receipts)
    gray = _deskew(gray)

    # 4. Denoise
    gray = cv2.fastNlMeansDenoising(gray, h=10)

    # 5. Adaptive thresholding (handles uneven lighting on receipts)
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=15,
        C=8
    )

    # 6. Morphological cleanup
    kernel = np.ones((1, 1), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    return binary


def _deskew(img: np.ndarray) -> np.ndarray:
    """Correct image skew using Hough transform."""
    try:
        coords = np.column_stack(np.where(img > 0))
        angle = cv2.minAreaRect(coords)[-1]

        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle

        if abs(angle) < 0.5:  # Skip tiny corrections
            return img

        (h, w) = img.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(
            img, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )
        return rotated
    except Exception:
        return img


def extract_text(processed_img: np.ndarray) -> str:
    """Run Tesseract OCR on preprocessed image."""
    config = "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,/$€£₹%:/-@&' "
    text = pytesseract.image_to_string(processed_img, config=config)
    return sanitize_text(text)


def parse_amount(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Extract the final/total amount and detect currency."""
    amount = None
    currency = None

    # Detect currency symbol first
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            currency = code
            break

    # Try to detect 3-letter currency codes
    currency_match = re.search(r'\b(USD|EUR|GBP|INR|JPY|CAD|AUD|SGD|AED|CHF)\b', text)
    if currency_match:
        currency = currency_match.group(1)

    # Find the total amount (prefer lines with TOTAL/GRAND)
    lines = text.upper().split('\n')
    for line in lines:
        if any(kw in line for kw in ['GRAND TOTAL', 'TOTAL DUE', 'AMOUNT DUE', 'BALANCE DUE']):
            for pattern in AMOUNT_PATTERNS:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    try:
                        amount = float(m.group(1).replace(',', ''))
                        return amount, currency
                    except ValueError:
                        pass

    # Fallback: find any TOTAL line
    for line in lines:
        if 'TOTAL' in line:
            for pattern in AMOUNT_PATTERNS:
                m = re.search(pattern, line, re.IGNORECASE)
                if m:
                    try:
                        amount = float(m.group(1).replace(',', ''))
                        return amount, currency
                    except ValueError:
                        pass

    # Last resort: find largest dollar amount in text
    all_amounts = []
    for pattern in AMOUNT_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            try:
                val = float(m.group(1).replace(',', ''))
                all_amounts.append(val)
            except (ValueError, IndexError):
                pass

    if all_amounts:
        amount = max(all_amounts)

    return amount, currency


def parse_date(text: str) -> Optional[str]:
    """Extract date from OCR text."""
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def parse_merchant(text: str) -> Optional[str]:
    """Extract merchant/store name from OCR text."""
    lines = [l.strip() for l in text.strip().split('\n') if l.strip()]

    # Check first 3 non-empty lines for merchant name (usually at the top)
    for line in lines[:3]:
        if len(line) > 2 and not re.match(r'^[\d\s\$\.\,\-]+$', line):
            # Filter out obvious non-merchant lines
            skip_words = ['receipt', 'invoice', 'order', 'date', 'time', 'thank']
            if not any(w in line.lower() for w in skip_words):
                # Clean up
                merchant = re.sub(r'[^\w\s&\'.,-]', '', line).strip()
                if merchant and len(merchant) > 1:
                    return merchant[:100]

    # Try patterns
    for pattern in MERCHANT_PATTERNS:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            return match.group(1).strip()[:100]

    return None


def parse_description(text: str) -> str:
    """Extract a summary description from the receipt."""
    lines = [l.strip() for l in text.split('\n') if l.strip() and len(l.strip()) > 3]
    # Use the first meaningful block as description
    meaningful = [l for l in lines if not re.match(r'^[\d\s\.\,\-\$]+$', l)]
    return ' | '.join(meaningful[:3])[:500]


def parse_expense_lines(text: str) -> List[OCRExpenseLine]:
    """Extract individual line items from receipt."""
    lines = []
    # Pattern: item description followed by amount
    line_pattern = re.compile(
        r'^(.+?)\s+\$?\s*(\d+(?:\.\d{2})?)\s*$',
        re.MULTILINE
    )
    seen = set()
    for match in line_pattern.finditer(text):
        desc = match.group(1).strip()
        amount_str = match.group(2)

        # Skip lines that are likely headers or totals
        skip = ['total', 'subtotal', 'tax', 'tip', 'discount', 'change', 'cash', 'card']
        if any(s in desc.lower() for s in skip):
            continue
        if desc in seen or len(desc) < 2:
            continue

        try:
            amount = float(amount_str)
            lines.append(OCRExpenseLine(description=desc[:200], amount=amount))
            seen.add(desc)
        except ValueError:
            pass

        if len(lines) >= 20:  # Cap at 20 line items
            break

    return lines


def calculate_confidence(result: dict) -> float:
    """Score OCR extraction confidence based on fields found."""
    score = 0.0
    if result.get("merchant_name"):
        score += 0.25
    if result.get("amount") is not None:
        score += 0.35
    if result.get("date"):
        score += 0.20
    if result.get("expense_lines"):
        score += 0.10
    if result.get("currency"):
        score += 0.10
    return round(score, 2)


async def process_receipt(image_bytes: bytes) -> OCRResult:
    """Full OCR pipeline: preprocess → extract → parse → return structured data."""
    try:
        # Preprocess
        processed = preprocess_image(image_bytes)

        # Extract raw text
        raw_text = extract_text(processed)

        if not raw_text.strip():
            return OCRResult(
                raw_text="",
                parsed_successfully=False,
                confidence=0.0
            )

        # Parse fields
        amount, currency = parse_amount(raw_text)
        merchant = parse_merchant(raw_text)
        date = parse_date(raw_text)
        description = parse_description(raw_text)
        expense_lines = parse_expense_lines(raw_text)

        result_dict = {
            "merchant_name": merchant,
            "amount": amount,
            "currency": currency,
            "date": date,
            "expense_lines": expense_lines,
        }
        confidence = calculate_confidence(result_dict)

        return OCRResult(
            raw_text=raw_text,
            merchant_name=merchant,
            amount=amount,
            currency=currency,
            date=date,
            description=description,
            expense_lines=expense_lines,
            confidence=confidence,
            parsed_successfully=confidence > 0.2
        )

    except Exception as e:
        logger.error(f"OCR processing failed: {e}", exc_info=True)
        return OCRResult(
            raw_text="",
            parsed_successfully=False,
            confidence=0.0
        )