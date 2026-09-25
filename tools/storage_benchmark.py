"""Measure durable Store writes on artificial ledgers, never user wallet state."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time
from dipbot.storage import Store


def benchmark(sizes=(100,1000,10000), repeats=5):
    rows=[]
    with tempfile.TemporaryDirectory(prefix='dipbot-storage-bench-') as directory:
        store=Store(Path(directory)/'state.json')
        for count in sizes:
            store.data={'closed_trades':[{'id':f'synthetic-{i}','entry_cost_usd':'0.1',
                'proceeds_usd':'0.11','gas_fee_wei':'1000000000000','closed_at':1700000000+i} for i in range(count)]}
            durations=[]
            for _ in range(repeats):
                start=time.perf_counter();store.save();durations.append((time.perf_counter()-start)*1000)
            loaded=Store(store.path)
            assert len(loaded.data['closed_trades'])==count
            rows.append({'rows':count,'bytes':store.path.stat().st_size,'samples_ms':durations,
                         'median_ms':statistics.median(durations),'max_ms':max(durations)})
    return {'synthetic':True,'includes_fsync':True,'sizes':rows,'repeats':repeats}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=benchmark()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
