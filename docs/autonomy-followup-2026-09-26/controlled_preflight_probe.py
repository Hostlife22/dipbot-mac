"""Explicit synthetic signal + HTTP 429 on a live PAPER market; never natural-cycle evidence."""
import json
from pathlib import Path
from unittest.mock import patch
from dipbot.strategy import Strategy
from tools.token_ui_paper_check import run
output=Path('/tmp/dipbot-followup-1to4-20260926/forced-preflight-probe')
original=Strategy.observe
injected=[]
def observe(strategy,price,now,**kwargs):
    action=original(strategy,price,now,**kwargs)
    if not injected and strategy.entry is None and not strategy.stopped:
        injected.append({'at':now,'price':str(price),'synthetic':True})
        return 'BUY'
    return action
try:
    with patch.object(Strategy,'observe',observe):
        result=run('0x681234B574Ac190Eaf58Bb12a5D61fB29bb07777',output,60,
            pool_address='0x99BA1075D94de62AE670cd616555D7bB7FA382b6',close_after=True,
            amount_usd='1',automatic_only=True,fee_usd='0.01',
            endpoint='https://bsc-rpc.publicnode.com',backup_rpc='https://bsc-dataseed.binance.org',
            dip='10',take_profit='15',stop_loss='15',slippage='5',dynamic='120',
            continue_after_sl=True,cooldown=30,trailing=0,preflight_fault='http429')
finally:
    path=output/'report.json'
    if path.exists():
        d=json.loads(path.read_text())
        d.update(automatic_only=False,natural_signals_only=False,synthetic_signals=injected,
                 source='Live BSC PAPER with one SYNTHETIC BUY signal and one SYNTHETIC HTTP429; not natural-cycle evidence')
        d['preflight_probe_passed']=bool(d.get('preflight_fault_injected') and d.get('preflight_notice_seen') and d.get('preflight_recovered_without_start') and d.get('test_restarts')==0 and not d.get('errors') and d.get('clean_stop'))
        path.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
