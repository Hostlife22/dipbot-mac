from decimal import Decimal as D

from dipbot.domain.signal_policy import SignalPolicy
from dipbot.domain.strategy import Settings, Strategy
from dipbot.domain.volatility import RollingVolatility


def test_rolling_sigma_matches_independent_batch_calculation_and_expiry():
    v = RollingVolatility()
    changes = []
    previous = None
    for i, p in enumerate(map(D, ["100", "102", "101", "105", "103", "104"])):
        if previous is not None:
            changes.append((i, (p / previous - 1) * 100))
        previous = p
        sigma = v.add(p, i, 3)
        values = [x for t, x in changes if t >= i - 3]
        if len(values) > 1:
            mean = sum(values) / len(values)
            expected = (sum((x - mean) ** 2 for x in values) / (len(values) - 1)).sqrt()
            assert abs(sigma - expected) < D("1e-20")


def test_duplicate_block_does_not_inflate_volatility_warmup():
    s = Strategy(Settings(), SignalPolicy(mode="volatility"))
    for i in range(20):
        assert s.observe(D(100), i * 0.1, observation_id="same") is None
    assert not s.volatility.rows


def test_volatility_floor_and_warmup_then_real_dip():
    s = Strategy(Settings(dip=D(3)), SignalPolicy(mode="volatility"))
    for i in range(12):
        assert s.observe(D(100), i * 0.1, observation_id=i) is None
    assert s.effective_dip == 3
    assert s.observe(D(96), 1.2, observation_id=12) == "BUY"
    s.reset_anchor()
    assert not s.volatility.rows and s.effective_dip == 3


def test_price_only_fallback_samples_at_most_once_per_second():
    s = Strategy(Settings(), SignalPolicy(mode="volatility"))
    for i in range(40):
        s.observe(D(100), i * 0.1)
    assert len(s.volatility.rows) == 3
