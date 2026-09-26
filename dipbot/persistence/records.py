"""Public persistence shapes; optional legacy fields are never fabricated."""

from typing import NotRequired, TypedDict

from dipbot.domain.records import (
    CostSettings,
    ExitSettings,
    PaperSettings,
    PoolRecord,
    SignalSettings,
    SizingSettings,
)


class PairSelection(TypedDict):
    router: str
    pair: str


class PreferencesRecord(TypedDict):
    version: int
    settings: dict[str, str]
    gas: str
    interval: str
    selection: NotRequired[PairSelection]
    pair_amounts: NotRequired[dict[str, str]]
    usd_pair_amounts: NotRequired[dict[str, str]]
    paper_policy: NotRequired[PaperSettings]
    entry_cost_policy: NotRequired[CostSettings]
    exit_policy: NotRequired[ExitSettings]
    signal_policy: NotRequired[SignalSettings]
    sizing: NotRequired[SizingSettings]
    adaptive_rpc: NotRequired[bool]
    record_market: NotRequired[bool]


class WalletToken(TypedDict):
    address: str
    router: str
    pair_name: str
    last_seen: int
    pool: PoolRecord


class WalletRegistry(TypedDict):
    version: int
    wallets: dict[str, dict[str, WalletToken]]


class DynamicProfile(TypedDict):
    name: str
    token_address: str
    trade_router: str
    trade_pool: str
    trade_fee: int
    converter_mode: str
    v3_quote_fee: int
    converter_route: list[str]
    roundtrip_loss_bps: int
    decimals: int
    created_at: int
    updated_at: int


class DynamicRegistry(TypedDict):
    version: int
    records: dict[str, DynamicProfile]
