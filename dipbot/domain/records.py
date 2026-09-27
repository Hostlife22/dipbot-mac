"""Wire/storage shapes. Decimal money is serialized as strings, raw amounts as integers."""

from decimal import Decimal
from typing import NotRequired, TypedDict


class ExitRetry(TypedDict):
    error: str
    attempt: int
    limit: int
    retry_at: float


class PaperAccounting(TypedDict):
    value: Decimal
    closed: int
    missing: int
    entry: str | None


class TradeDetail(TypedDict, total=False):
    quantity: str
    entry_gross_usd: str | None
    entry_fee_usd: str | None
    entry_total_usd: str | None
    buy_price_usd: str | None
    sell_price_usd: str | None
    exit_gross_usd: str | None
    exit_fee_usd: str | None
    complete: bool
    net_usd: str | None


class PoolRecord(TypedDict):
    token_is_0: bool
    address: str
    token: str
    quote: str
    token_decimals: int
    quote_decimals: int
    router: str
    fee: int


class RateMark(TypedDict, total=False):
    usd: str
    observed_at: float
    source: str


class FeeSummary(TypedDict):
    wei: str | None
    usd: str | None


class TransactionRequest(TypedDict, total=False):
    chainId: int
    nonce: int
    value: int
    gas: int
    gasPrice: int
    to: str


class PositionRecord(TypedDict, total=False):
    execution_price: str
    entry_gas_hashes: list[str] | None
    amount: int
    entry: str
    pool: PoolRecord
    opened_at: float
    peak_price: str
    cost_quote: str | None
    entry_cost_usd: str | None
    entry_fees: FeeSummary
    entry_rate: RateMark | None


class TransactionRecord(TypedDict, total=False):
    transaction_id: str
    signal_cycle_id: str
    hash: str
    label: str
    nonce: int
    status: str
    stage: str
    prepared_at: int
    submitted_at: int
    receipt_at: int
    prepared_block: int
    broadcast_route: str
    request: TransactionRequest
    block: int
    block_hash: str
    gas_fee_wei: int
    gas_usd: str | None
    gas_usd_rate: RateMark | None
    receipt_review: str


class OperationRecord(TypedDict, total=False):
    operation_id: str
    signal_cycle_id: str
    wallet: str
    description: str
    started: int
    transactions: list[TransactionRecord]
    outcome: str


class SweepReport(TypedDict, total=False):
    status: str
    sold: list[str]
    failed: list[str]
    skipped: list[str]
    remaining: dict[str, int]
    unknown: list[str]
    error: str
    needs_reconciliation: bool


class OpenEstimate(TypedDict):
    at: float
    value_usd: str | None
    pnl_usd: str | None
    excludes_exit_gas: bool


class RpcHealthRow(TypedDict):
    source: str
    samples: int
    median_ms: float | None
    consecutive_errors: int
    preferred: bool


class UsdSummary(TypedDict):
    scope: NotRequired[str]
    value: str | None
    closed: int
    missing: int
    includes_gas: bool


class PriceContext(TypedDict):
    source: str
    rpc_source: str
    same_block_cache: bool
    block: int | None
    block_timestamp: int | None
    quote: str


class GasRecord(TypedDict, total=False):
    wallet: str
    label: str
    wei: str
    usd: str | None
    rate: RateMark | None
    block: int | None
    status: str


class ClosedTrade(TypedDict, total=False):
    wallet: str
    pool: str
    token: str
    quote: str
    closed_at: int
    inventory_matches: bool
    cost_quote: str | None
    proceeds_quote: str
    entry_cost_usd: str | None
    entry_gas_hashes: list[str] | None
    exit_gas_hashes: list[str]
    proceeds_usd: str | None
    exit_gas_usd: str | None
    exit_rate: RateMark | None
    exit_fees: FeeSummary
    net_usd: str | None
    sold_raw: int
    residual_raw: int
    source_pools: list[str]


class SizingSettings(TypedDict):
    unit: str
    reserve_bnb: str


class PaperSettings(TypedDict):
    gas_units: int
    latency_seconds: float
    fee_quote: str


class CostSettings(TypedDict):
    maximum_pct: str
    roundtrip_gas: int


class ExitSettings(TypedDict):
    continue_after_risk_exit: bool
    tp_sl_basis: str
    trailing_pct: str
    max_hold_seconds: float
    cooldown_seconds: float


class SignalSettings(TypedDict):
    mode: str
    window_seconds: float
    rebound_pct: str
    max_block_age: float
    volatility_multiplier: str
