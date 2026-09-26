from types import SimpleNamespace as NS
import pytest
from dipbot.observability.cycle_trace import CycleTrace, signal_cycle
from dipbot.execution.errors import UncertainTransaction
from test_execution import trader, Function


def test_signal_trace_correlates_signature_durable_intent_and_receipt(trader):
    events=[]
    worker=NS(mode='LIVE',live=trader,record_market=lambda event,**kw:events.append((event,kw)))
    with signal_cycle(worker,'BUY',{'number':42}):
        trader.begin('test');trader.send(Function(),'test');trader.finish()
    row=events[0][1]
    assert [r['stage'] for r in row['stages']]==[
        'signal','gas_estimated','transaction_built','signed','intent_persisted',
        'broadcast_ack','receipt_validated','completed']
    assert [r['ms'] for r in row['stages']]==sorted(r['ms'] for r in row['stages'])
    assert row['signal_block']==42 and row['block_to_signal_ms'] is None
    assert row['error_type'] is None and trader.cycle_trace is None
    assert trader.owner not in str(events)


def test_uncertain_send_keeps_failure_and_does_not_invent_receipt(trader):
    events=[];trader.chain.w3.eth.fail_send=True
    worker=NS(mode='LIVE',live=trader,record_market=lambda event,**kw:events.append(kw))
    with pytest.raises(UncertainTransaction):
        with signal_cycle(worker,'BUY',None):
            trader.begin('test');trader.send(Function(),'test')
    row=events[0]
    assert row['error_type']=='UncertainTransaction'
    assert row['stages'][-1]['stage']=='failed'
    assert 'receipt_validated' not in [s['stage'] for s in row['stages']]
    assert 'private RPC' not in str(row)
    assert trader.store.data['operation']


def test_diagnostic_failure_does_not_change_execution_exception():
    def fail(*a,**kw):raise OSError('diagnostic failure')
    worker=NS(mode='PAPER',live=None,record_market=fail)
    with pytest.raises(ValueError,match='original'):
        with signal_cycle(worker,'BUY',None):raise ValueError('original')
    assert worker.cycle_trace is None
    trace=CycleTrace('BUY','PAPER')
    for _ in range(1000):trace.mark('quote')
    assert len(trace.stages)==64 and trace.truncated


def test_report_separates_paper_and_live_and_marks_incomplete(tmp_path):
    import json
    from tools.cycle_latency_report import summarize
    path=tmp_path/'synthetic.jsonl'
    rows=[{'event':'cycle_latency','mode':'PAPER','stages':[
        {'stage':'quote','ms':20},{'stage':'completed','ms':30}],'block_to_signal_ms':100},
        {'event':'cycle_latency','mode':'LIVE','stages':[
        {'stage':'signed','ms':20},{'stage':'receipt_validated','ms':300},
        {'stage':'completed','ms':350}]},
        {'event':'cycle_latency','mode':'LIVE','error_type':'TimeoutError'},
        {'event':'end','dropped':1}]
    path.write_text('\n'.join(json.dumps(row) for row in rows))
    report=summarize([path])
    assert report['cycles']==3 and report['failed_cycles']=={'LIVE':1}
    assert report['metrics']['LIVE.signal_to_last_receipt_validated_ms']['p50']==300
    assert not any('PAPER' in key and 'receipt' in key for key in report['metrics'])
    assert report['incomplete_recordings']==[path.name]
