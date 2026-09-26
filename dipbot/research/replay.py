"""Causal offline stress replay. Fixed costs are assumptions, not executable quotes."""
from dataclasses import dataclass
from decimal import Decimal as D
import math

from dipbot.domain.strategy import Strategy


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


def pool_fee_bps(header, override=None):
    """Pancake fee tiers are millionths; one basis point is 100 millionths."""
    if override is not None:
        try:
            value = D(str(override))
        except ArithmeticError as exc:
            raise ValueError("Некорректная комиссия replay") from exc
        if not value.is_finite() or not 0 <= value < 10000:
            raise ValueError('Некорректная комиссия replay')
        return value
    pool = header.get('pool') or {}
    if isinstance(pool, str):
        return D(25)  # Old exported tapes kept only the pool address.
    if not isinstance(pool, dict):
        raise ValueError('Повреждены метаданные пула')
    if pool.get('router') == 'V3':
        try:
            fee = D(str(pool.get('fee', 'NaN')))
        except ArithmeticError as exc:
            raise ValueError('Повреждён тариф V3 в записи') from exc
        if fee not in (100, 500, 2500, 10000):
            raise ValueError('В записи нет известного тарифа V3; задайте --fee-bps явно')
        return fee / 100
    if pool.get('router') in (None, 'V2'):
        return D(25)  # Legacy tapes without pool metadata retain the documented V2 assumption.
    raise ValueError('Неизвестный router; задайте --fee-bps явно')


def replay(samples, settings, policy, costs=None, *, size_unit='quote', requested_amount=None, exit_policy=None):
    if settings.min_swaps:
        raise ValueError('Replay цен не содержит Swap-события для фильтра активности')
    if size_unit not in ('quote','usd'):
        raise ValueError('Неизвестная единица replay AMOUNT')
    requested_amount = settings.amount if requested_amount is None else D(str(requested_amount))
    if not requested_amount.is_finite() or requested_amount <= 0:
        raise ValueError('Некорректная сумма replay')
    costs = costs or ReplayCosts()
    strategy = Strategy(settings, policy, exit_policy)
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
                amount = pending['amount']
                output = amount / price * costs.factor
                bound = amount / reference * (1-settings.buy_tolerance/100)
                if output < bound:
                    rejected.append({'t':now, 'side':side, 'reason':'snapshot_minOut'})
                    cooldown = now+5
                    strategy.reset_anchor()
                else:
                    quantity = output
                    cost = amount+costs.gas_quote
                    strategy.bought(price, now=now)  # PAPER anchors exits to the fresh pre-fill spot; minOut keeps the signal snapshot.
                    trades.append({'t':now, 'side':'BUY', 'signal_t':pending['signal_t'],
                                   'price':str(price), 'quantity':str(quantity), 'cost':str(cost)})
                pending = None
            else:
                proceeds = quantity*price*costs.factor-costs.gas_quote
                pnl = proceeds-cost
                realized += pnl
                trades.append({'t':now, 'side':'SELL', 'reason':side, 'signal_t':pending['signal_t'],
                               'price':str(price), 'pnl':str(pnl)})
                strategy.sold(price, side, now=now)
                quantity = cost = D(0)
                pending = None
        if pending is None and not strategy.stopped and now >= cooldown:
            block = sample.get('block')
            identity = (block, sample.get('block_hash')) if block is not None else None
            exit_return = ((quantity*price*costs.factor-costs.gas_quote)/cost-1)*100 if quantity and cost else None
            action = strategy.observe(price, now, observation_id=identity, exit_return=exit_return)
            if action:
                # Even zero latency executes on a subsequent observation; never on future data.
                amount = requested_amount
                if action == 'BUY' and size_unit == 'usd':
                    rate = D(str(sample.get('quote_usd') or '0'))
                    if not rate.is_finite() or rate <= 0:
                        rejected.append({'t':now,'side':'BUY','reason':'missing_usd_rate'})
                        strategy.reset_anchor()
                        cooldown = now+5
                        continue
                    amount = requested_amount/rate
                pending = {'side':action, 'price':price, 'at':now+costs.latency_seconds,
                           'signal_t':now, 'amount':amount}
        equity = realized + (quantity*price*costs.factor-costs.gas_quote-cost if quantity else D(0))
        high_equity = max(high_equity,equity)
        drawdown = max(drawdown,high_equity-equity)
    return {'model':'causal_fixed_cost_stress_v1', 'samples':count, 'policy':policy.export(),
            'assumptions':{'fee_bps':str(costs.fee_bps),'impact_bps':str(costs.impact_bps),
                           'tax_bps':str(costs.tax_bps),'gas_quote':str(costs.gas_quote),
                           'latency_seconds':costs.latency_seconds, 'size_unit':size_unit, 'requested_amount':str(requested_amount),
                           'limitations':'No pool depth, gas estimation, MEV, nonce, receipt or sellability simulation'},
            'realized_quote':str(realized), 'max_drawdown_quote':str(drawdown),
            'open_quantity':str(quantity), 'pending':pending is not None,
            'stopped':strategy.stopped, 'trades':trades, 'rejected':rejected}
