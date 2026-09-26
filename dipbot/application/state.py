"""State owned exclusively by the sequential Worker; no additional executor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dipbot.domain.cost_policy import CostPolicy
from dipbot.domain.paper_policy import PaperPolicy
from dipbot.domain.records import ExitRetry, OpenEstimate, PaperAccounting, TradeDetail
from dipbot.domain.sizing import SizingPolicy
from dipbot.domain.strategy import D, Settings, Strategy
from dipbot.execution.paper import PaperTrader
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain, Pool
from dipbot.market.gap_recovery import GapRecovery
from dipbot.market.head_feed import HeadFeed, HeadSchedule
from dipbot.market.market_monitor import MarketMonitor
from dipbot.market.rpc_health import RpcHealth
from dipbot.research.market_tape import MarketTape


@dataclass
class Connections:
    @property
    def reader(self) -> Chain:
        if self.chain is None:
            raise ValueError("Сначала подключите HTTP RPC")
        return self.chain

    @property
    def backup(self) -> Chain:
        if self.backup_chain is None:
            raise ValueError("Резервный RPC не подключён")
        return self.backup_chain

    chain: Chain | None = None
    backup_chain: Chain | None = None
    broadcast_chain: Chain | None = None
    head_feed: HeadFeed | None = None
    gap_recovery: GapRecovery | None = None
    head_schedule: HeadSchedule = field(default_factory=lambda: HeadSchedule())
    backup_until: float = 0.0
    rpc_health: RpcHealth = field(default_factory=lambda: RpcHealth())
    adaptive_rpc: bool = False
    last_market_header: dict[str, Any] | None = None
    backup_verified_pool: Pool | None = None
    market_source: str = "BSC"


@dataclass
class MarketState:
    @property
    def selected(self) -> Pool:
        if self.pool is None:
            raise ValueError("Пул не выбран")
        return self.pool

    @property
    def price(self) -> D:
        if self.current_price is None:
            raise ValueError("Нет текущей котировки")
        return self.current_price

    pool: Pool | None = None
    tick: int = 0
    current_price: D | None = None
    price_time: float = 0.0
    interval: float = 0.1
    discovery_generation: int = 0
    pool_generation: int | None = None
    quote_failures: int = 0
    quote_unavailable: bool = False


@dataclass
class SessionState:
    @property
    def executor(self) -> LiveTrader:
        if self.live is None:
            raise ValueError("Нужен LIVE-режим и кошелёк в Keychain")
        return self.live

    mode: str = "DEMO"
    running: bool = False
    strategy: Strategy = field(default_factory=lambda: Strategy(Settings()))
    paper: PaperTrader = field(default_factory=lambda: PaperTrader(D("2")))
    paper_context: tuple[str, ...] | None = None
    live: LiveTrader | None = None
    sizing: SizingPolicy = field(default_factory=lambda: SizingPolicy())
    cost_policy: CostPolicy = field(default_factory=lambda: CostPolicy())
    paper_policy: PaperPolicy = field(default_factory=lambda: PaperPolicy(latency_seconds=0))
    gas_gwei: D = field(default_factory=lambda: D(".1"))
    requested_amount: D | None = None
    paper_usd: PaperAccounting = field(
        default_factory=lambda: PaperAccounting(value=D(0), closed=0, missing=0, entry=None)
    )
    entry_retry_at: float = 0.0
    entry_notice: str = ""
    halt_reason: str = ""
    exit_retry: ExitRetry | None = None
    trade_detail: TradeDetail | None = None
    open_estimate: OpenEstimate | None = None
    execution_monitor: MarketMonitor | None = None


@dataclass
class RecordingState:
    recorder: MarketTape | None = None
    recorder_notice: bool = False


class StateAccess:
    """Compatibility accessors for existing UI/diagnostics; state has one owner."""

    connections: Connections

    @property
    def chain(self) -> Chain | None:
        return self.connections.chain

    @chain.setter
    def chain(self, value: Chain | None) -> None:
        self.connections.chain = value

    @property
    def backup_chain(self) -> Chain | None:
        return self.connections.backup_chain

    @backup_chain.setter
    def backup_chain(self, value: Chain | None) -> None:
        self.connections.backup_chain = value

    @property
    def broadcast_chain(self) -> Chain | None:
        return self.connections.broadcast_chain

    @broadcast_chain.setter
    def broadcast_chain(self, value: Chain | None) -> None:
        self.connections.broadcast_chain = value

    @property
    def head_feed(self) -> HeadFeed | None:
        return self.connections.head_feed

    @head_feed.setter
    def head_feed(self, value: HeadFeed | None) -> None:
        self.connections.head_feed = value

    @property
    def gap_recovery(self) -> GapRecovery | None:
        return self.connections.gap_recovery

    @gap_recovery.setter
    def gap_recovery(self, value: GapRecovery | None) -> None:
        self.connections.gap_recovery = value

    @property
    def head_schedule(self) -> HeadSchedule:
        return self.connections.head_schedule

    @head_schedule.setter
    def head_schedule(self, value: HeadSchedule) -> None:
        self.connections.head_schedule = value

    @property
    def backup_until(self) -> float:
        return self.connections.backup_until

    @backup_until.setter
    def backup_until(self, value: float) -> None:
        self.connections.backup_until = value

    @property
    def rpc_health(self) -> RpcHealth:
        return self.connections.rpc_health

    @rpc_health.setter
    def rpc_health(self, value: RpcHealth) -> None:
        self.connections.rpc_health = value

    @property
    def adaptive_rpc(self) -> bool:
        return self.connections.adaptive_rpc

    @adaptive_rpc.setter
    def adaptive_rpc(self, value: bool) -> None:
        self.connections.adaptive_rpc = value

    @property
    def last_market_header(self) -> dict[str, Any] | None:
        return self.connections.last_market_header

    @last_market_header.setter
    def last_market_header(self, value: dict[str, Any] | None) -> None:
        self.connections.last_market_header = value

    @property
    def backup_verified_pool(self) -> Pool | None:
        return self.connections.backup_verified_pool

    @backup_verified_pool.setter
    def backup_verified_pool(self, value: Pool | None) -> None:
        self.connections.backup_verified_pool = value

    @property
    def market_source(self) -> str:
        return self.connections.market_source

    @market_source.setter
    def market_source(self, value: str) -> None:
        self.connections.market_source = value

    market: MarketState

    @property
    def pool(self) -> Pool | None:
        return self.market.pool

    @pool.setter
    def pool(self, value: Pool | None) -> None:
        self.market.pool = value

    @property
    def tick(self) -> int:
        return self.market.tick

    @tick.setter
    def tick(self, value: int) -> None:
        self.market.tick = value

    @property
    def current_price(self) -> D | None:
        return self.market.current_price

    @current_price.setter
    def current_price(self, value: D | None) -> None:
        self.market.current_price = value

    @property
    def price_time(self) -> float:
        return self.market.price_time

    @price_time.setter
    def price_time(self, value: float) -> None:
        self.market.price_time = value

    @property
    def interval(self) -> float:
        return self.market.interval

    @interval.setter
    def interval(self, value: float) -> None:
        self.market.interval = value

    @property
    def discovery_generation(self) -> int:
        return self.market.discovery_generation

    @discovery_generation.setter
    def discovery_generation(self, value: int) -> None:
        self.market.discovery_generation = value

    @property
    def pool_generation(self) -> int | None:
        return self.market.pool_generation

    @pool_generation.setter
    def pool_generation(self, value: int | None) -> None:
        self.market.pool_generation = value

    @property
    def quote_failures(self) -> int:
        return self.market.quote_failures

    @quote_failures.setter
    def quote_failures(self, value: int) -> None:
        self.market.quote_failures = value

    @property
    def quote_unavailable(self) -> bool:
        return self.market.quote_unavailable

    @quote_unavailable.setter
    def quote_unavailable(self, value: bool) -> None:
        self.market.quote_unavailable = value

    session: SessionState

    @property
    def mode(self) -> str:
        return self.session.mode

    @mode.setter
    def mode(self, value: str) -> None:
        self.session.mode = value

    @property
    def running(self) -> bool:
        return self.session.running

    @running.setter
    def running(self, value: bool) -> None:
        self.session.running = value

    @property
    def strategy(self) -> Strategy:
        return self.session.strategy

    @strategy.setter
    def strategy(self, value: Strategy) -> None:
        self.session.strategy = value

    @property
    def paper(self) -> PaperTrader:
        return self.session.paper

    @paper.setter
    def paper(self, value: PaperTrader) -> None:
        self.session.paper = value

    @property
    def paper_context(self) -> tuple[str, ...] | None:
        return self.session.paper_context

    @paper_context.setter
    def paper_context(self, value: tuple[str, ...] | None) -> None:
        self.session.paper_context = value

    @property
    def live(self) -> LiveTrader | None:
        return self.session.live

    @live.setter
    def live(self, value: LiveTrader | None) -> None:
        self.session.live = value

    @property
    def sizing(self) -> SizingPolicy:
        return self.session.sizing

    @sizing.setter
    def sizing(self, value: SizingPolicy) -> None:
        self.session.sizing = value

    @property
    def cost_policy(self) -> CostPolicy:
        return self.session.cost_policy

    @cost_policy.setter
    def cost_policy(self, value: CostPolicy) -> None:
        self.session.cost_policy = value

    @property
    def paper_policy(self) -> PaperPolicy:
        return self.session.paper_policy

    @paper_policy.setter
    def paper_policy(self, value: PaperPolicy) -> None:
        self.session.paper_policy = value

    @property
    def gas_gwei(self) -> D:
        return self.session.gas_gwei

    @gas_gwei.setter
    def gas_gwei(self, value: D) -> None:
        self.session.gas_gwei = value

    @property
    def requested_amount(self) -> D | None:
        return self.session.requested_amount

    @requested_amount.setter
    def requested_amount(self, value: D | None) -> None:
        self.session.requested_amount = value

    @property
    def paper_usd(self) -> PaperAccounting:
        return self.session.paper_usd

    @paper_usd.setter
    def paper_usd(self, value: PaperAccounting) -> None:
        self.session.paper_usd = value

    @property
    def entry_retry_at(self) -> float:
        return self.session.entry_retry_at

    @entry_retry_at.setter
    def entry_retry_at(self, value: float) -> None:
        self.session.entry_retry_at = value

    @property
    def entry_notice(self) -> str:
        return self.session.entry_notice

    @entry_notice.setter
    def entry_notice(self, value: str) -> None:
        self.session.entry_notice = value

    @property
    def halt_reason(self) -> str:
        return self.session.halt_reason

    @halt_reason.setter
    def halt_reason(self, value: str) -> None:
        self.session.halt_reason = value

    @property
    def exit_retry(self) -> ExitRetry | None:
        return self.session.exit_retry

    @exit_retry.setter
    def exit_retry(self, value: ExitRetry | None) -> None:
        self.session.exit_retry = value

    @property
    def trade_detail(self) -> TradeDetail | None:
        return self.session.trade_detail

    @trade_detail.setter
    def trade_detail(self, value: TradeDetail | None) -> None:
        self.session.trade_detail = value

    @property
    def open_estimate(self) -> OpenEstimate | None:
        return self.session.open_estimate

    @open_estimate.setter
    def open_estimate(self, value: OpenEstimate | None) -> None:
        self.session.open_estimate = value

    @property
    def execution_monitor(self) -> MarketMonitor | None:
        return self.session.execution_monitor

    @execution_monitor.setter
    def execution_monitor(self, value: MarketMonitor | None) -> None:
        self.session.execution_monitor = value

    recording: RecordingState

    @property
    def recorder(self) -> MarketTape | None:
        return self.recording.recorder

    @recorder.setter
    def recorder(self, value: MarketTape | None) -> None:
        self.recording.recorder = value

    @property
    def recorder_notice(self) -> bool:
        return self.recording.recorder_notice

    @recorder_notice.setter
    def recorder_notice(self, value: bool) -> None:
        self.recording.recorder_notice = value
