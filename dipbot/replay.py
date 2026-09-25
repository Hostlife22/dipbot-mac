"""Causal offline stress replay. Fixed costs are assumptions, not executable quotes."""
from dataclasses import dataclass
from decimal import Decimal as D
import math

from .strategy import Strategy


@dataclass(frozen=True)
class ReplayCosts:
    fee_bps: D = D(25)
    impact_bps: D = D(0)
    tax_bps: D = D(0)
    gas_quote: D = D(0)
    latency_seconds: float = .25

    def __post_init__(self):
        for value in (self.fee_bps, self.impact_bps, self.tax_bps, self.gas_quote):
            if not value.is_finite() or value < 0:
                raise ValueError('Некорректная модель расходов')
        if self.fee_bps+self.impact_bps+self.tax_bps >= 10000:
            raise ValueError('Расходы должны быть меньше 100%')
        if not math.isfinite(self.latency_seconds) or not 0 <= self.latency_seconds <= 30:
            raise ValueError('Задержка исполнения должна быть от 0 до 30 секунд')

    @property
    def factor(self):
        return 1-(self.fee_bps+self.impact_bps+self.tax_bps)/10000


def replay(samples, settings, policy, costs=None):
    costs = costs or ReplayCosts()
    strategy = Strategy(settings, policy)
    pending = None
    quantity = cost = realized = D(0)
    high_equity = drawdown = D(0)
    cooldown = float('-inf')
    previous_time = float('-inf')
    last_price = None
    trades, rejected = [], []
    count = 0
    for sample in samples:
        now, price = float(sample['t']), D(str(sample['price']))
        if not math.isfinite(now) or now <= previous_time or not price.is_finite() or price <= 0:
            raise ValueError('Replay требует строго возрастающее время и положительные конечные цены')
        previous_time = now
        count += 1
        last_price = price
        if sample.get('tradable', True) is not True:
            continue
        if pending and now >= pending['at']:
            side, reference = pending['side'], pending['price']
            if now-pending['at'] > 30:
                rejected.append({'t':now, 'side':side, 'reason':'execution_deadline'})
                pending = None
                strategy.reset_anchor()
            elif side == 'BUY':
                output = settings.amount / price * costs.factor
                bound = settings.amount / reference * (1-settings.buy_tolerance/100)
                if output < bound:
                    rejected.append({'t':now, 'side':side, 'reason':'snapshot_minOut'})
                    cooldown = now+5
                    strategy.reset_anchor()
                else:
                    quantity = output
                    cost = settings.amount+costs.gas_quote
                    strategy.bought(reference)  # Matches PAPER's signal-price TP/SL reference.
                    trades.append({'t':now, 'side':'BUY', 'signal_t':pending['signal_t'],
                                   'price':str(price), 'quantity':str(quantity), 'cost':str(cost)})
                pending = None
            else:
                proceeds = quantity*price*costs.factor-costs.gas_quote
                pnl = proceeds-cost
                realized += pnl
                trades.append({'t':now, 'side':'SELL', 'reason':side, 'signal_t':pending['signal_t'],
                               'price':str(price), 'pnl':str(pnl)})
                strategy.sold(price, side)
                quantity = cost = D(0)
                pending = None
        if pending is None and not strategy.stopped and now >= cooldown:
            block = sample.get('block')
            identity = (block, sample.get('block_hash')) if block is not None else None
            action = strategy.observe(price, now, observation_id=identity)
            if action:
                # Even zero latency executes on a subsequent observation; never on future data.
                pending = {'side':action, 'price':price, 'at':now+costs.latency_seconds, 'signal_t':now}
        equity = realized + (quantity*price*costs.factor-costs.gas_quote-cost if quantity else D(0))
        high_equity = max(high_equity,equity)
        drawdown = max(drawdown,high_equity-equity)
    return {'model':'causal_fixed_cost_stress_v1', 'samples':count, 'policy':policy.export(),
            'assumptions':{'fee_bps':str(costs.fee_bps),'impact_bps':str(costs.impact_bps),
                           'tax_bps':str(costs.tax_bps),'gas_quote':str(costs.gas_quote),
                           'latency_seconds':costs.latency_seconds,
                           'limitations':'No pool depth, gas estimation, MEV, nonce, receipt or sellability simulation'},
            'realized_quote':str(realized), 'max_drawdown_quote':str(drawdown),
            'open_quantity':str(quantity), 'pending':pending is not None,
            'stopped':strategy.stopped, 'trades':trades, 'rejected':rejected}
