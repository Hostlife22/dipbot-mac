"""Shared preferences fixtures/builders."""
import pytest
from dipbot.persistence import preferences
from dipbot.persistence.storage import Store


def values():
    return {'version': 1, 'settings': {'amount': '0.03', 'dip': '4', 'take_profit': '6',
            'stop_loss': '7', 'slippage': '2', 'dynamic': '150', 'max_roundtrip_loss': '3', 'min_swaps': '0'}, 'gas': '0.1', 'interval': '0.103'}
