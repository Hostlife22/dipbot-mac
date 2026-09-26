from dataclasses import asdict
from types import SimpleNamespace
import pytest

from dipbot.worker import Worker, safe_error
from dipbot.storage import Store
from dipbot.strategy import D
from dipbot.chain import Pool, WBNB, USDT, address


def config(mode="DEMO"):
    return {"mode": mode, "settings": {"amount": "1", "dip": "3", "take_profit": "2",
            "stop_loss": "5", "slippage": "0", "dynamic": "0"}, "interval": 0.1, "gas": "0.1"}


def test_demo_full_cycle_offline(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    messages = []
    worker.log.connect(messages.append)
    worker.command("start", config())
    for _ in range(80):
        worker.observe()
    worker.close_position("STOP")
    assert any("BUY" in message for message in messages)
    assert any("SELL" in message for message in messages)
    assert worker.paper.position == 0
    assert worker.chain is None and worker.live is None


def test_manual_buy_cannot_double_position(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.command("buy", config())
    position = worker.paper.position
    with pytest.raises(ValueError, match="уже открыта"):
        worker.command("buy", config())
    assert worker.paper.position == position


def test_cannot_change_connection_with_open_position(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.command("buy", config())
    with pytest.raises(ValueError, match="закройте"):
        worker.command("connect", {"rpc": "https://example.com"})


def test_changed_target_cannot_trade_old_pool(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.chain = SimpleNamespace()
    worker.pool = Pool(address("0x"+"12"*20), "V2", address(USDT), address(WBNB), 18, 18, False)
    data = config("PAPER") | {"token": WBNB, "pool": worker.pool.address}
    with pytest.raises(ValueError, match="изменились"):
        worker.configure(data)


def test_live_sell_uses_only_tracked_amount(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.mode = "LIVE"
    worker.pool = Pool(address("0x"+"12"*20), "V2", address(USDT), address(WBNB), 18, 18, False)
    sold = []
    worker.live = SimpleNamespace(owner=address("0x"+"34"*20), begin=lambda _: None,
        finish=lambda: None, swap=lambda pool, amount, buy, tolerance, **kwargs: sold.append(amount))
    worker.chain = SimpleNamespace(balance=lambda *args: 1000, price=lambda _: D("1.2"))
    worker.set_position(200, D(1))
    worker.strategy.bought(D(1))
    worker.close_position("STOP")
    assert sold == [200]
    assert not worker.position()
    assert worker.strategy.stopped
    assert worker.strategy.base == D("1.2")


def test_error_redaction():
    assert "https" not in safe_error(ValueError("https://node/private-api-key"))
    assert "11"*32 not in safe_error(ValueError("bad key " + "11"*32))



def test_live_buy_uses_signal_guard_and_post_receipt_reference(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.mode = "LIVE"
    worker.pool = Pool(address("0x"+"12"*20), "V2", address(USDT), address(WBNB), 18, 18, False)
    worker.current_price = D(2)
    worker.chain = SimpleNamespace(price=lambda _: D("2.1"))
    guards = []
    def swap(pool, amount, buy, tolerance, *, signal_minimum):
        guards.append(signal_minimum)
        return 10**16
    worker.live = SimpleNamespace(owner=address("0x"+"34"*20), begin=lambda _: None,
        finish=lambda: None, swap=swap)
    worker.open_position()
    assert guards == [9_620_000_000_000_000]  # Default BUY tolerance: 5 - 120/100 = 3.8%.
    assert worker.strategy.entry == D("2.1")
    assert worker.position()["entry"] == "2.1"
    assert worker.position()["amount"] == 10**16


def test_slow_poll_setting_rejected(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    with pytest.raises(ValueError, match="0.55"):
        worker.configure(config() | {"interval": 1})


def test_confirmed_buy_price_read_failure_keeps_holdings_and_latch(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.mode = "LIVE"
    worker.pool = Pool(address("0x"+"12"*20), "V2", address(USDT), address(WBNB), 18, 18, False)
    worker.current_price = D(2)
    def failed_price(_):
        raise TimeoutError()
    worker.chain = SimpleNamespace(price=failed_price)
    def begin(_):
        worker.store.data["operation"] = {"description": "BUY"}
    def finish():
        pytest.fail("Failed post-receipt read must not clear operation")
    worker.live = SimpleNamespace(owner=address("0x"+"34"*20), begin=begin,
        finish=finish, swap=lambda *args, **kwargs: 10**16)
    with pytest.raises(TimeoutError):
        worker.open_position()
    reloaded = Store(worker.store.path)
    assert reloaded.data["positions"][worker.position_key()]["amount"] == 10**16
    assert reloaded.data["operation"]


def test_paper_tp_reference_is_spot_not_slippage_execution(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.current_price = D(100)
    worker.open_position()
    assert worker.strategy.entry == 100
    assert worker.paper.cost / worker.paper.position > worker.strategy.entry
