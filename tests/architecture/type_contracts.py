"""Compile-time conformance checks, run by mypy rather than pytest."""

from dipbot.application.ports import MarketReader, RateSource, SecretStore, StateStore, TradeExecutor
from dipbot.execution.accounting import RateBook
from dipbot.execution.trader import LiveTrader
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.persistence.vault import Vault


def market_adapter(value: Chain) -> MarketReader:
    return value


def state_adapter(value: Store) -> StateStore:
    return value


def secret_adapter(value: Vault) -> SecretStore:
    return value


def execution_adapter(value: LiveTrader) -> TradeExecutor:
    return value


def rates_adapter(value: RateBook) -> RateSource:
    return value
