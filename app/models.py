"""Pydantic models for API responses."""

from typing import Optional, List
from pydantic import BaseModel


class AccountResponse(BaseModel):
    plaid_account_id: str
    item_id: str
    name: Optional[str] = None
    official_name: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    mask: Optional[str] = None
    current_balance: Optional[float] = None
    available_balance: Optional[float] = None
    currency_code: Optional[str] = "USD"


class TransactionResponse(BaseModel):
    transaction_id: str
    account_id: str
    item_id: str
    amount: float
    iso_currency_code: Optional[str] = "USD"
    name: Optional[str] = None
    merchant_name: Optional[str] = None
    pending: bool = False
    pending_transaction_id: Optional[str] = None
    authorized_date: Optional[str] = None
    date: str
    category: Optional[str] = None
    category_id: Optional[str] = None
    personal_finance_category: Optional[str] = None
    payment_channel: Optional[str] = None
    transaction_type: Optional[str] = None
    location: Optional[str] = None


class HoldingResponse(BaseModel):
    account_id: str
    item_id: str
    security_id: str
    institution_price: Optional[float] = None
    institution_price_as_of: Optional[str] = None
    institution_value: Optional[float] = None
    cost_basis: Optional[float] = None
    quantity: Optional[float] = None
    currency_code: Optional[str] = "USD"


class SecurityResponse(BaseModel):
    security_id: str
    isin: Optional[str] = None
    cusip: Optional[str] = None
    sedol: Optional[str] = None
    ticker_symbol: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    close_price: Optional[float] = None
    close_price_as_of: Optional[str] = None
    currency_code: Optional[str] = "USD"
    is_cash_equivalent: bool = False


class InvestmentTransactionResponse(BaseModel):
    investment_transaction_id: str
    account_id: str
    item_id: str
    security_id: Optional[str] = None
    date: str
    name: Optional[str] = None
    quantity: Optional[float] = None
    amount: Optional[float] = None
    price: Optional[float] = None
    fees: Optional[float] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    currency_code: Optional[str] = "USD"


class SyncLogResponse(BaseModel):
    item_id: str
    sync_type: str
    status: str
    added_count: int = 0
    modified_count: int = 0
    removed_count: int = 0
    error_message: Optional[str] = None
    started_at: str
    completed_at: Optional[str] = None


class SyncResult(BaseModel):
    """Result from a sync operation."""
    item_id: str
    sync_type: str
    status: str
    added: int = 0
    modified: int = 0
    removed: int = 0
    error: Optional[str] = None


class ItemResponse(BaseModel):
    item_id: str
    institution_id: Optional[str] = None
    institution_name: Optional[str] = None
    created_at: str
    accounts: List[AccountResponse] = []


class ErrorResponse(BaseModel):
    detail: str
    plaid_error_code: Optional[str] = None
