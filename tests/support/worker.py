"""Shared worker fixtures/builders."""
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from dipbot.application.worker import Worker, safe_error
from dipbot.persistence.storage import Store
from dipbot.domain.strategy import D
from dipbot.market.chain import Pool, WBNB, USDT, address


def config(mode="DEMO"):
    return {"mode": mode, "settings": {"amount": "1", "dip": "3", "take_profit": "2",
            "stop_loss": "5", "slippage": "0", "dynamic": "0"}, "interval": 0.1, "gas": "0.1"}
