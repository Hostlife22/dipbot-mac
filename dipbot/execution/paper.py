"""Virtual execution and position accounting, with no RPC or signing."""

from __future__ import annotations

from decimal import Decimal as D


class PaperTrader:
    def __init__(self, slippage: D) -> None:
        self.slippage = slippage
        self.position = D(0)
        self.cost = D(0)
        self.realized = D(0)

    def buy(self, amount: D, price: D) -> D:
        if self.position:
            raise ValueError("Бумажная позиция уже открыта")
        self.cost = amount
        self.position = amount / (price * (1 + self.slippage / 100))
        return amount / self.position

    def sell(self, price: D) -> D:
        proceeds = self.position * price * (1 - self.slippage / 100)
        pnl = proceeds - self.cost
        self.realized += pnl
        self.position = self.cost = D(0)
        return pnl

    def buy_quoted(self, cost: D, received: D) -> D:
        if self.position or cost <= 0 or received <= 0:
            raise ValueError("Некорректная PAPER-покупка")
        self.cost, self.position = cost, received
        return cost / received

    def sell_quoted(self, proceeds: D, fee: D = D(0)) -> D:
        if not self.position or proceeds <= 0 or not fee.is_finite() or fee < 0:
            raise ValueError("Некорректная PAPER-продажа")
        pnl = proceeds - self.cost - fee
        self.realized += pnl
        self.position = self.cost = D(0)
        return pnl
