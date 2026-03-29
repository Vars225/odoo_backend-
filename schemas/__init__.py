from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List, Any
from enum import Enum
from datetime import datetime


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    admin = "admin"
    manager = "manager"
    employee = "employee"


class ExpenseStatus(str, Enum):
    pending = "pending"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ApprovalRuleType(str, Enum):
    sequential = "sequential"       # All approvers must approve in order
    percentage = "percentage"       # X% of approvers must approve
    specific_approver = "specific"  # Specific person approves → auto-approved
    hybrid = "hybrid"               # percentage OR specific approver


class PaymentStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"


# ─── Auth Schemas ──────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    company_name: str = Field(..., min_length=2, max_length=200)
    country: str = Field(..., min_length=2, max_length=100)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ─── User Schemas ──────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.employee
    manager_id: Optional[str] = None


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[UserRole] = None
    manager_id: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    role: UserRole
    company_id: str
    manager_id: Optional[str] = None
    created_at: datetime


# ─── Company Schemas ───────────────────────────────────────────────────────────

class CompanyResponse(BaseModel):
    id: str
    name: str
    country: str
    base_currency: str
    created_at: datetime


# ─── Expense Schemas ───────────────────────────────────────────────────────────

class ExpenseCategory(str, Enum):
    travel = "travel"
    meals = "meals"
    accommodation = "accommodation"
    software = "software"
    hardware = "hardware"
    marketing = "marketing"
    infrastructure = "infrastructure"
    general = "general"
    other = "other"


class CreateExpenseRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3, description="ISO 4217 currency code")
    category: ExpenseCategory
    description: str = Field(..., min_length=5, max_length=1000)
    date: datetime
    merchant_name: Optional[str] = None
    project_code: Optional[str] = None
    is_manager_approver: bool = False  # Manager must approve first

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v):
        return v.upper()


class ExpenseResponse(BaseModel):
    id: str
    user_id: str
    user_name: Optional[str] = None
    company_id: str
    amount_original: float
    currency_original: str
    amount_converted: float
    company_currency: str
    category: str
    description: str
    merchant_name: Optional[str] = None
    project_code: Optional[str] = None
    receipt_url: Optional[str] = None
    status: ExpenseStatus
    is_manager_approver: bool
    current_step: int
    ocr_data: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class ExpenseListResponse(BaseModel):
    expenses: List[ExpenseResponse]
    total: int
    page: int
    page_size: int


# ─── Approval Flow Schemas ────────────────────────────────────────────────────

class ApprovalStep(BaseModel):
    step: int
    role: str  # "manager" | "finance" | "director" | specific user_id
    approver_id: Optional[str] = None  # if specific person
    label: str = ""  # human-readable label


class ApprovalRule(BaseModel):
    type: ApprovalRuleType
    percentage: Optional[float] = Field(None, ge=0, le=100)
    special_approver_id: Optional[str] = None  # CFO user_id
    description: Optional[str] = None


class CreateApprovalFlowRequest(BaseModel):
    name: str
    steps: List[ApprovalStep] = Field(..., min_length=1)
    rules: ApprovalRule
    applies_to_amounts_above: Optional[float] = None  # threshold


class ApprovalFlowResponse(BaseModel):
    id: str
    company_id: str
    name: str
    steps: List[ApprovalStep]
    rules: ApprovalRule
    applies_to_amounts_above: Optional[float] = None
    created_at: datetime


# ─── Approval Action Schemas ──────────────────────────────────────────────────

class ApprovalActionRequest(BaseModel):
    comment: Optional[str] = Field(None, max_length=1000)


class ApprovalResponse(BaseModel):
    id: str
    expense_id: str
    approver_id: str
    approver_name: Optional[str] = None
    step: int
    status: ApprovalStatus
    comment: Optional[str] = None
    timestamp: datetime


class PendingApprovalItem(BaseModel):
    expense: ExpenseResponse
    approval_id: str
    step: int
    step_label: str


# ─── Payment Schemas ───────────────────────────────────────────────────────────

class ConfirmPaymentRequest(BaseModel):
    expense_id: str


class PaymentResponse(BaseModel):
    id: str
    expense_id: str
    stripe_payment_id: str
    client_secret: Optional[str] = None
    status: PaymentStatus
    amount: float
    currency: str
    created_at: datetime


# ─── OCR Schemas ──────────────────────────────────────────────────────────────

class OCRExpenseLine(BaseModel):
    description: str
    amount: Optional[float] = None
    quantity: Optional[int] = None


class OCRResult(BaseModel):
    raw_text: str
    merchant_name: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    expense_lines: List[OCRExpenseLine] = []
    confidence: float = 0.0
    parsed_successfully: bool = False


# ─── Notification Schemas ─────────────────────────────────────────────────────

class NotificationType(str, Enum):
    expense_submitted = "expense_submitted"
    expense_approved = "expense_approved"
    expense_rejected = "expense_rejected"
    payment_processed = "payment_processed"
    approval_required = "approval_required"


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    type: NotificationType
    title: str
    message: str
    expense_id: Optional[str] = None
    read: bool = False
    created_at: datetime


# ─── Generic Response Schemas ─────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None