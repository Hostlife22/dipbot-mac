"""Bounded real-market PAPER soak. No wallet access, signatures or broadcast.

A stopped strategy stays stopped; natural signals are never forced or fabricated.
Progress and final reports distinguish elapsed monitoring from active strategy time.
"""
import argparse
from collections import deque
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time
import os
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from dipbot.chain import Chain, address
from dipbot.storage import Store, Vault
from dipbot.strategy import Settings, D
from dipbot.worker import Worker
from dipbot.trader import LiveTrader
from dipbot.usd import UsdRate
from dipbot.diagnostics import Diagnostics, resource_snapshot
from tools.read_only_probe import guard_provider


def forbidden(*args, **kwargs):
    raise RuntimeError('PAPER soak forbids wallet access and LIVE execution')


def run(directory, markets, seconds, *, autonomous=False, amount_usd=None, fee_usd="0", trailing="0.75", take_profit="1", stop_loss="1"):
    directory.mkdir(parents=True,exist_ok=False)
    app=QCoreApplication.instance() or QCoreApplication([])
    diagnostics=Diagnostics(directory/'diagnostics')
    workers=[];rows=[];fx_sources=[];started=time.monotonic();resources=deque(maxlen=2880)
    report={'mode':'PAPER','seconds_requested':seconds,'transactions_sent':0,'natural_signals_only':True,
            'test_driver_restarts':0,'continue_after_risk_exit':autonomous,'markets':rows,'started_at':int(time.time())}
    def save(final=False):
        r=resource_snapshot()
        try:
            r['current_rss_bytes']=int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())],text=True,timeout=2).strip())*1024
        except (ValueError,OSError,subprocess.SubprocessError):r['current_rss_bytes']=None
        r['elapsed_seconds']=time.monotonic()-started;resources.append(r)
        result={**report,'elapsed_seconds':time.monotonic()-started,'resources':list(resources),'final':final}
        saved=Store(directory/('report.json' if final else 'progress.json'));saved.data=result;saved.save()
        diagnostics.checkpoint()
    try:
        with patch.object(Vault,'get',forbidden),patch.object(Vault,'save',forbidden),patch.object(LiveTrader,'send',forbidden):
            for i,(token,pool_address) in enumerate(markets):
                row={'token':token,'pool':pool_address,'prices':0,'signals':{},'exits':{},'fills':{},'errors':[],
                     'active_seconds':0,'running':False,'recent_prices':[]}
                rows.append(row)
                chain=Chain('https://bsc-dataseed.binance.org');calls=guard_provider(chain.w3.provider)
                pool=chain.verify_pool(pool_address,token)
                worker=Worker(Store(directory/f'market-{i}.json'));worker.chain=chain;worker.pool=pool
                fee_quote = D(0)
                if amount_usd is not None or D(fee_usd):
                    fx = UsdRate(app);fx_sources.append(fx)
                    fx.changed.connect(lambda fx=fx,worker=worker: worker.rates.update(fx.token,fx.current(),fx.received_at))
                    fx.set_token(pool.quote)
                    deadline=time.monotonic()+60
                    while fx.current() is None and time.monotonic()<deadline:
                        app.processEvents();time.sleep(.02)
                    if fx.current() is None:raise TimeoutError('Fresh base/USD unavailable')
                    fee_quote = D(fee_usd)/fx.current()
                    row['initial_quote_usd']=str(fx.current())
                row['paper_fee_quote']=str(fee_quote)
                row['paper_fee_usd_at_start']=fee_usd
                workers.append(worker)
                recent=deque(maxlen=128)
                def event(kind,value,row=row,recent=recent):
                    if kind=='price':
                        row['prices']+=1;recent.append(value);row['recent_prices']=list(recent)
                    elif kind=='status':
                        row['running']=value['running'];row['position']=value['position'];row['quote_unavailable']=value.get('quote_unavailable')
                    elif kind=='error':row['errors']=(row['errors']+[str(value)])[-20:]
                    elif kind=='trade_marker':row['fills'][value['side']]=row['fills'].get(value['side'],0)+1
                def log(line,row=row):
                    if 'Архив рынка неполный' in line:row['archive_incomplete']=True
                    if 'PAPER SELL: ' in line:
                        reason=line.split('PAPER SELL: ')[-1];row['exits'][reason]=row['exits'].get(reason,0)+1
                worker.event.connect(event);worker.log.connect(log)
                policy={'mode':'window','window_seconds':60,'rebound_pct':'0.1','max_block_age':5}
                exits={'tp_sl_basis':'quote','continue_after_risk_exit':autonomous,'trailing_pct':trailing,'max_hold_seconds':180,'cooldown_seconds':5}
                settings={k:str(v) for k,v in asdict(Settings(amount=D(amount_usd or '.00003'),dip=D('.5'),take_profit=D(take_profit),stop_loss=D(stop_loss))).items() if k!='max_gap'}
                row['settings']=settings;row['signal_policy']=policy;row['exit_policy']=exits
                worker.command('start',{'mode':'PAPER','settings':settings,'interval':.3,'gas':'.1',
                    'token':token,'pool':pool.address,'router':pool.router,'signal_policy':policy,
                    'exit_policy':exits,'record_market':True,
                    'sizing':{'unit':'usd' if amount_usd is not None else 'quote'},
                    'paper_policy':{'fee_quote':str(fee_quote),'latency_seconds':.25}})
                row['rpc_methods']=calls
            report['setup_seconds']=time.monotonic()-started
            started=time.monotonic();report['started_at']=int(time.time())
            for worker in workers:worker.start()
            last=checkpoint=time.monotonic()
            save()
            while time.monotonic()-started<seconds:
                app.processEvents();time.sleep(.02)
                now=time.monotonic()
                for worker,row in zip(workers,rows):
                    if worker.running:row['active_seconds']+=now-last
                last=now
                if now-checkpoint>=30:
                    save();checkpoint=now
                    print(json.dumps({'elapsed_seconds':round(now-started),'markets':[{'prices':r['prices'],'fills':r['fills'],'running':r['running']} for r in rows]}),flush=True)
            for worker in workers:worker.stop_event.set()
            deadline=time.monotonic()+120
            while any(w.running or w.stop_event.is_set() for w in workers) and time.monotonic()<deadline:
                app.processEvents();time.sleep(.02)
            report['clean_stop']=all(not w.paper.position and not w.running and not w.stop_event.is_set() for w in workers)
            report['passed']=report['clean_stop'] and all(r['prices']>0 and not r['errors'] for r in rows)
    except Exception as exc:
        report['failure_type']=type(exc).__name__;report['passed']=False
    finally:
        for worker in workers:worker.quit_event.set()
        report['workers_joined']=all([w.wait() for w in workers])
        # Join is the completion barrier; stop_event is cleared before closing.
        report['clean_stop']=all(not w.paper.position and not w.running for w in workers)
        report['passed']=bool(report.get('passed') and report['workers_joined'] and report['clean_stop'])
        app.processEvents();save(final=True);diagnostics.close(clean=report.get('passed',False))
    print(json.dumps({'passed':report.get('passed'), 'output':str(directory/'report.json')}),flush=True)
    return 0 if report.get('passed') else 1

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--seconds',type=int,default=7200)
    p.add_argument('--market',action='append',required=True,help='TOKEN:POOL (repeatable)')
    p.add_argument('--autonomous',action='store_true')
    p.add_argument('--amount-usd')
    p.add_argument('--fee-usd',default='0',help='Fixed fee model converted to base at start; not actual gas')
    p.add_argument('--trailing',default='0.75');p.add_argument('--take-profit',default='1');p.add_argument('--stop-loss',default='1')
    a=p.parse_args()
    if a.amount_usd is not None and (not D(a.amount_usd).is_finite() or not 0<D(a.amount_usd)<=1):p.error('USD amount must be in (0,1]')
    if not D(a.fee_usd).is_finite() or not 0<=D(a.fee_usd)<=1:p.error('Invalid fee model')
    if not 1<=a.seconds<=86400:p.error('seconds must be between 1 and 86400')
    markets=[tuple(address(x) for x in item.split(':')) for item in a.market]
    if any(len(m)!=2 for m in markets):p.error('TOKEN:POOL required')
    raise SystemExit(run(a.output,markets,a.seconds,autonomous=a.autonomous,amount_usd=a.amount_usd,fee_usd=a.fee_usd,trailing=a.trailing,take_profit=a.take_profit,stop_loss=a.stop_loss))
