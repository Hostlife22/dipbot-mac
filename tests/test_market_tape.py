import json
from dataclasses import asdict
from pathlib import Path
from dipbot.research.market_tape import MarketTape
from dipbot.application.worker import Worker
from dipbot.persistence.storage import Store
from test_worker import config


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def test_tape_records_only_public_fields_and_closes_with_counts(tmp_path):
    tape=MarketTape(tmp_path,{'mode':'PAPER','rpc':'SECRET_RPC','key':'SECRET_KEY','settings':{'rpc':'SECRET_NESTED'}})
    for i in range(5): tape.record('price',price=str(i+1),block=i,rpc='SECRET_RPC',signed='SECRET_RAW')
    assert tape.close()
    rows=read(tape.path)
    assert rows[0]['version']==2 and rows[-1]['event']=='end'
    assert rows[-1]['written']==5 and rows[-1]['dropped']==0
    assert [r['sequence'] for r in rows[1:-1]]==list(range(1,6))
    assert 'SECRET' not in tape.path.read_text()
    assert tape.path.stat().st_mode & 0o777 == 0o600


def test_size_limit_reports_missing_data_instead_of_blocking_trading(tmp_path):
    tape=MarketTape(tmp_path,{'mode':'DEMO'},max_bytes=2048,max_total_bytes=2048,capacity=2048)
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


def rotated(directory, **kwargs):
    tape=MarketTape(directory,{'mode':'DEMO'},max_bytes=2048,capacity=2048,**kwargs)
    for i in range(100):tape.record('price',price=str(i+1),block=i)
    assert tape.close()
    return tape


def test_rotation_preserves_global_sequence_and_full_replay(tmp_path):
    from tools.replay_market import load
    tape=rotated(tmp_path)
    assert len(tape.paths)>1 and tape.dropped==0 and tape.completed
    assert all(p.stat().st_size<=2048 for p in tape.paths)
    header,samples=load(tape.path)
    assert [r['sequence'] for r in samples]==list(range(1,101))
    assert len({read(p)[0]['session_id'] for p in tape.paths})==1
    assert read(tape.paths[-1])[-1]['session_complete']


def test_missing_middle_and_loading_partial_session_are_rejected(tmp_path):
    import pytest
    from tools.replay_market import load
    tape=rotated(tmp_path)
    with pytest.raises(ValueError,match='первый файл'):load(tape.paths[1])
    tape.paths[1].unlink()
    with pytest.raises(FileNotFoundError):load(tape.path)


def test_tampered_link_cannot_silently_shorten_session(tmp_path):
    import pytest
    from tools.replay_market import load
    tape=rotated(tmp_path)
    rows=read(tape.path);rows[-1]['next_file']=None
    tape.path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match='цепочка'):load(tape.path)
    rows[-1]['next_file']='../outside.jsonl'
    tape.path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    with pytest.raises(ValueError,match='ссылка'):load(tape.path)


def test_total_quota_bounds_all_parts_and_reports_loss(tmp_path):
    import pytest
    from tools.replay_market import load
    tape=rotated(tmp_path,max_total_bytes=6000)
    assert len(tape.paths)>1 and tape.dropped>0
    assert sum(p.stat().st_size for p in tape.paths)<=6000
    with pytest.raises(ValueError):load(tape.path)


def test_concurrent_recorders_reserve_shared_quota(tmp_path):
    a=MarketTape(tmp_path,{},max_bytes=2048,max_total_bytes=4096,capacity=200)
    b=MarketTape(tmp_path,{},max_bytes=2048,max_total_bytes=4096,capacity=200)
    for i in range(100):
        a.record('price',price=str(i));b.record('price',price=str(i))
    assert a.close() and b.close()
    assert sum(p.stat().st_size for p in tmp_path.glob('*.jsonl'))<=4096
    assert a.dropped+b.dropped>0
    assert not any(p.parent==tmp_path for p in MarketTape._leases)


def test_legacy_v1_recording_remains_readable(tmp_path):
    from tools.replay_market import load
    path=tmp_path/'old.jsonl'
    rows=[{'event':'header','version':1},{'event':'price','sequence':1,'t':0,'price':'1'},
          {'event':'end','written':1,'last_sequence':1,'dropped':0}]
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    assert len(load(path)[1])==1


def test_replay_cannot_silently_ignore_recorded_gas_model(tmp_path,monkeypatch,capsys):
    import pytest,sys
    from tools.replay_market import main
    tape=MarketTape(tmp_path,{'settings':{},'paper_policy':{'gas_units':200000}})
    tape.record('price',price='1');assert tape.close()
    monkeypatch.setattr(sys,'argv',['replay_market',str(tape.path),'--output',str(tmp_path/'out.json')])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2 and '--gas-quote' in capsys.readouterr().err


def test_rotation_io_failure_is_explicit_and_releases_reservation(tmp_path,monkeypatch):
    import pytest
    from tools.replay_market import load
    tape=MarketTape(tmp_path,{},max_bytes=2048,capacity=2048)
    def failed(*args):raise OSError('synthetic disk failure')
    monkeypatch.setattr(tape,'open_segment',failed)
    for i in range(100):tape.record('price',price=str(i))
    assert tape.close()
    assert tape.error_type=='OSError' and not tape.completed
    assert tape.path not in MarketTape._leases
    with pytest.raises(ValueError):load(tape.path)
