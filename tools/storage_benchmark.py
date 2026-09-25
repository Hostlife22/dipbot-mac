"""Measure durable Store writes on artificial ledgers, never user wallet state."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
from decimal import Decimal as D
from types import SimpleNamespace
from dipbot.accounting import record_close, record_gas, closed_summary, expense_summary
from dipbot.storage import Store


def realistic_ledger(store, count):
    """Use production accounting writers and full rows; all addresses are synthetic."""
    owner='0x'+'11'*20
    pool=SimpleNamespace(address='0x'+'22'*20, token='0x'+'33'*20,
                         quote='0x'+'44'*20, quote_decimals=18)
    rate={'usd':'1','observed_at':1700000000,'source':'synthetic benchmark'}
    store.data={}
    for i in range(count):
        buy='0x'+format(2*i+1,'064x');sell='0x'+format(2*i+2,'064x')
        rows=[]
        for tx_hash, label in ((buy,'BUY'),(sell,'SELL')):
            row={'hash':tx_hash,'label':label,'gas_fee_wei':10**12,
                 'gas_usd':'.001','gas_usd_rate':rate,'block':17000000+i,'status':1}
            record_gas(store,owner,row);rows.append(row)
        position={'entry_cost_usd':'1.001','cost_quote':'1','entry_gas_hashes':[buy]}
        record_close(store,owner,pool,position,11*10**17,
                     {'transactions':[rows[-1]]},rate)
    return owner


def benchmark(sizes=(100,1000,10000,100000), repeats=5):
    rows=[]
    with tempfile.TemporaryDirectory(prefix='dipbot-storage-bench-') as directory:
        store=Store(Path(directory)/'state.json')
        for count in sizes:
            owner=realistic_ledger(store,count)
            durations=[];summaries=[]
            for index in range(repeats):
                # A durable outstanding operation must survive a growing history.
                store.data['operation']={'kind':'SELL','wallet':owner,'transactions':[
                    {'hash':'0x'+'aa'*32,'nonce':index,'status':'prepared'}]}
                start=time.perf_counter();store.save();durations.append((time.perf_counter()-start)*1000)
                start=time.perf_counter()
                summary=closed_summary(store,owner);expenses=expense_summary(store,owner)
                summaries.append((time.perf_counter()-start)*1000)
                assert D(summary['value']) == D('.098')*count
                assert expenses['allocation_complete']
                assert D(expenses['realized_less_other_gas_usd']) == D('.098')*count
            start=time.perf_counter();loaded=Store(store.path);load_ms=(time.perf_counter()-start)*1000
            assert len(loaded.data['closed_trades'])==count
            assert len(loaded.data['gas_ledger'])==2*count
            assert loaded.data['operation']==store.data['operation']
            rows.append({'closed_trades':count,'gas_receipts':2*count,
                         'bytes':store.path.stat().st_size,'samples_ms':durations,
                         'median_ms':statistics.median(durations),'max_ms':max(durations),
                         'load_ms':load_ms,'summary_median_ms':statistics.median(summaries)})
    return {'synthetic':True,'schema':'production accounting writers',
            'includes_fsync':True,'sizes':rows,'repeats':repeats,
            'limitations':'Single process, temporary filesystem, no long-run or concurrent GUI guarantee'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=benchmark()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
