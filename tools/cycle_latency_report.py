"""Summarize observed per-signal stages; never infer inclusion from PAPER fills."""
import argparse
from collections import defaultdict, deque
import json
import math
from pathlib import Path


def summarize(paths):
    samples=defaultdict(lambda:deque(maxlen=10000))
    counts=defaultdict(int);failures=defaultdict(int);incomplete=[]
    cycles=0
    for path in paths:
        end=None
        with Path(path).open() as stream:
            for line in stream:
                row=json.loads(line)
                if row.get('event')=='end':end=row
                if row.get('event')!='cycle_latency':continue
                cycles+=1
                mode=row['mode']
                if row.get('error_type'):
                    failures[mode]+=1
                    continue
                if row.get('truncated'):continue
                stages=row['stages']
                if not stages or stages[-1]['stage']!='completed':continue
                metrics={'signal_to_application_complete_ms':stages[-1]['ms']}
                for stage in stages:
                    name=stage['stage']
                    # Repeated stages retain the final occurrence, including approves.
                    if name in ('quote','signed','receipt_validated','broadcast_ack',
                                'preflight_started','activity_checked','entry_screened',
                                'paper_delay_finished','fill_price_read','fill_quote_received','execution_applied'):
                        metrics['signal_to_last_'+name+'_ms']=stage['ms']
                for before, after in zip(stages, stages[1:]):
                    metrics['phase_'+before['stage']+'_to_'+after['stage']+'_ms'] = after['ms']-before['ms']
                if row.get('block_to_signal_ms') is not None:
                    metrics['approx_block_timestamp_to_signal_ms']=row['block_to_signal_ms']
                for name,value in metrics.items():
                    if type(value) not in (int,float) or not math.isfinite(value) or value<0:continue
                    action = {'BUY':'BUY', 'TAKE_PROFIT':'SELL', 'STOP_LOSS':'SELL',
                              'TRAILING_STOP':'SELL', 'TIME_EXIT':'SELL',
                              'CONTROLLED_BUY':'CONTROLLED_BUY',
                              'CONTROLLED_SELL':'CONTROLLED_SELL'}.get(row.get('action'))
                    for scope in ([mode, mode+'.'+action] if action else [mode]):
                        key=scope+'.'+name
                        samples[key].append(value);counts[key]+=1
        if not end or end.get('dropped'):
            incomplete.append(Path(path).name)
    result={}
    for key,values in samples.items():
        values=sorted(values)
        result[key]={'count':counts[key],'window':len(values),**{
            label:values[max(0,math.ceil(len(values)*q)-1)]
            for label,q in [('p50',.5),('p95',.95),('p99',.99)]}}
    return {'cycles':cycles,'failed_cycles':dict(failures),'metrics':result,
            'incomplete_recordings':incomplete,
            'limits':'Receipt validation is local observation, not exact block inclusion time. Block timestamp comparison depends on local wall clock. PAPER has no signature or inclusion.'}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('recordings',type=Path,nargs='+');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report=summarize(a.recordings)
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
