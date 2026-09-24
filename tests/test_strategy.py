import pytest
from dipbot.strategy import D, Settings, Strategy, raw_amount, minimum_out, snapshot_minimum
from dipbot.trader import PaperTrader


def test_dip_take_profit_reanchor():
    strategy = Strategy(Settings())
    assert strategy.observe(D(100), 0) is None
    assert strategy.observe(D(102), 0.1) is None
    assert strategy.observe(D(99), 0.2) is None
    assert strategy.observe(D(98), 0.3) == "BUY"
    strategy.bought(D(98))
    assert strategy.observe(D(99), 0.4) is None
    assert strategy.observe(D(100), 0.5) == "TAKE_PROFIT"
    strategy.sold(D(100), "TAKE_PROFIT")
    assert strategy.base == 100 and not strategy.stopped


def test_stop_loss_stops_reentry():
    strategy = Strategy(Settings())
    strategy.bought(D(100))
    assert strategy.observe(D(95), 1) == "STOP_LOSS"
    strategy.sold(D(95), "STOP_LOSS")
    assert strategy.observe(D(90), 2) is None
    assert strategy.stopped


def test_feed_gap_resets_buy_but_still_exits_open_position():
    strategy = Strategy(Settings())
    strategy.observe(D(100), 0)
    assert strategy.observe(D(80), 20) is None
    assert strategy.base == 80
    strategy.bought(D(80))
    assert strategy.observe(D(70), 40) == "STOP_LOSS"


def test_old_observation_ignored():
    s = Strategy(Settings())
    s.observe(D(100), 10)
    assert s.observe(D(50), 9) is None
    assert s.last_price == 100


@pytest.mark.parametrize("field,value", [("amount", "0"), ("dip", "100"), ("stop_loss", "0"),
      ("slippage", "21"), ("amount", "NaN"), ("dip", "Infinity"), ("dynamic", "-1")])
def test_invalid_settings(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: D(value)})


def test_amounts_round_down_and_never_zero():
    assert raw_amount(D("1.23456789"), 6) == 1234567
    assert raw_amount(D("0.02"), 18) == 20_000_000_000_000_000
    with pytest.raises(ValueError):
        raw_amount(D("0.0000001"), 6)
    assert minimum_out(12345, D("0.5")) == 12283
    with pytest.raises(ValueError):
        minimum_out(1, D("20"))
    assert Settings().buy_tolerance == D("0.5")
    huge = 10**70 + 123456789
    assert minimum_out(huge, D("0.5")) == huge * 995 // 1000
    assert raw_amount(D("1.123456789012345678901234567890123456"), 36) == 1123456789012345678901234567890123456


def test_paper_accounts_slippage_both_directions():
    broker = PaperTrader(D(2))
    assert broker.buy(D(102), D(1)) == D("1.02")
    assert broker.position == 100
    assert broker.sell(D(1)) == -4
    assert broker.position == 0 and broker.realized == -4


@pytest.mark.parametrize("prices,base,streak", [
    ([100, 99, 98], "98", 0),
    ([100, 99, 99.5], "99.5", 0),
    ([100, 99, 99, 98], "98", 0),
    ([100, 99, 99], "100", 1),
    ([100, 99, 99.5, 99], "99.5", 1),
])
def test_original_reanchor_vectors(prices, base, streak):
    strategy = Strategy(Settings())
    for i, price in enumerate(prices):
        assert strategy.observe(D(str(price)), i / 10) is None
    assert strategy.base == D(base)
    assert strategy.down_streak == streak


def test_buy_checked_before_second_down_reanchor():
    strategy = Strategy(Settings())
    strategy.observe(D(100), 0)
    strategy.observe(D("98.5"), 0.1)
    assert strategy.observe(D(97), 0.2) == "BUY"
    assert strategy.base == 100


@pytest.mark.parametrize("gap,action", [(0.55, "BUY"), (0.551, None)])
def test_gap_boundary(gap, action):
    strategy = Strategy(Settings())
    strategy.observe(D(100), 0)
    assert strategy.observe(D(90), gap) == action


def test_dynamic_above_slippage_clamps_to_zero():
    assert Settings(dynamic=D(201)).buy_tolerance == 0
    assert Settings(slippage=D(0), dynamic=D(150)).buy_tolerance == 0


def test_snapshot_guard_includes_fees_and_impact_in_tolerance():
    # 100 quote at 2 quote/token: 50 tokens spot; router offers only 49.
    bound = snapshot_minimum(100 * 10**6, D(2), 6, 18, D("0.5"))
    assert bound == 49_750_000_000_000_000_000
    assert bound > minimum_out(49 * 10**18, D("0.5"))
    assert snapshot_minimum(10**18, D("0.5"), 18, 6, D(0)) == 2_000_000
    with pytest.raises(ValueError):
        snapshot_minimum(1, D(10), 18, 6, D(0))
