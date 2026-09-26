"""Shared recovery fixtures/builders."""

from types import SimpleNamespace

from dipbot.application.worker import Worker
from dipbot.domain.assets import WBNB
from dipbot.domain.strategy import D
from dipbot.market.chain import Pool, address
from dipbot.persistence.storage import Store

OWNER = address("0x" + "34" * 20)


POOL = Pool(address("0x" + "12" * 20), "V2", address("0x" + "56" * 20), address(WBNB), 18, 18, True)


def setup_worker(tmp_path):
    worker = Worker(Store(tmp_path / "state.json"))
    worker.mode = "LIVE"
    worker.pool = POOL
    worker.live = SimpleNamespace(owner=OWNER)
    worker.set_position(200, D("1.2"))
    worker.strategy.bought(D("1.2"))
    return worker


def sweep_worker(tmp_path):
    worker = setup_worker(tmp_path)
    balances = {POOL.token: 200}
    sent = []
    worker.chain = SimpleNamespace(
        balance=lambda token, owner: balances.get(token, 0), verify_pool=lambda *_: POOL, quote=lambda *_: 190
    )
    worker.live.begin = lambda description: sent.append(description)
    worker.live.finish = lambda: worker.store.save()
    worker.live.swap = lambda *args, **kwargs: balances.update({POOL.token: 0})
    worker.command = lambda *args: None  # Final balance UI report only.
    return worker, sent
