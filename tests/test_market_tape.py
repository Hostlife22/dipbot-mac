import json
from dataclasses import asdict
from pathlib import Path
from dipbot.market_tape import MarketTape
from dipbot.worker import Worker
from dipbot.storage import Store
from test_worker import config


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def test_tape_records_only_public_fields_and_closes_with_counts(tmp_path):
    tape=MarketTape(tmp_path,{'mode':'PAPER','rpc':'SECRET_RPC','key':'SECRET_KEY','settings':{'rpc':'SECRET_NESTED'}})
    for i in range(5): tape.record('price',price=str(i+1),block=i,rpc='SECRET_RPC',signed='SECRET_RAW')
    assert tape.close()
    rows=read(tape.path)
    assert rows[0]['version']==1 and rows[-1]['event']=='end'
    assert rows[-1]['written']==5 and rows[-1]['dropped']==0
    assert [r['sequence'] for r in rows[1:-1]]==list(range(1,6))
    assert 'SECRET' not in tape.path.read_text()
    assert tape.path.stat().st_mode & 0o777 == 0o600


def test_size_limit_reports_missing_data_instead_of_blocking_trading(tmp_path):
    tape=MarketTape(tmp_path,{'mode':'DEMO'},max_bytes=2048,capacity=2048)
    for i in range(100): tape.record('price',price=str(i),block=i)
    assert tape.close()
    rows=read(tape.path)
    assert tape.path.stat().st_size<=2048 and rows[-1]['dropped']>0
    assert rows[-1]['written']+rows[-1]['dropped']==100


def test_worker_writes_demo_signals_and_execution_without_network(tmp_path):
    worker=Worker(Store(tmp_path/'state.json'))
    worker.command('start',config() | {'record_market':True})
    tape=worker.recorder
    for _ in range(15): worker.observe()
    worker.close_position('STOP')
    assert tape.close()
    rows=read(tape.path)
    assert any(r['event']=='signal' and r['action']=='BUY' for r in rows)
    assert any(r['event']=='execution' and r['side']=='BUY' for r in rows)
    assert worker.chain is None and worker.live is None
    from tools.replay_market import load
    header, observations = load(tape.path)
    assert observations and all(r['event']=='observation' for r in observations)


def test_loader_rejects_dropped_or_truncated_recording(tmp_path):
    import pytest
    from tools.replay_market import load
    tape=MarketTape(tmp_path,{'mode':'DEMO'})
    tape.record('price',price='1')
    tape.close()
    header,samples=load(tape.path)
    assert len(samples)==1
    rows=read(tape.path)
    rows[-1]['written']+=1
    tape.path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match='Счётчики'): load(tape.path)
