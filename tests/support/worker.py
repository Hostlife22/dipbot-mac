"""Shared worker fixtures/builders."""


def config(mode="DEMO"):
    return {
        "mode": mode,
        "settings": {
            "amount": "1",
            "dip": "3",
            "take_profit": "2",
            "stop_loss": "5",
            "slippage": "0",
            "dynamic": "0",
        },
        "interval": 0.1,
        "gas": "0.1",
    }
