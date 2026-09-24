"""Bounded static evidence for post-send errors, cleanup and Sweep outer handlers."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect
from tools.full_static_audit import TABLES

RANGES={
    'sell_wait_status':(0x14209802b,0x1420980ca),
    'sell_normal_close_error':(0x14209829a,0x1420982fa),
    'sell_exception_close_error':(0x142098561,0x142098610),
    'sell_run_call_failure':(0x14209b800,0x14209b890),
    'target_exception_handler':(0x14209bffd,0x14209c418),
    'base_exception_handler':(0x14209d613,0x14209d920),
    'worker':(0x140917680,0x1409184e5),
    'gui_error':(0x14091a140,0x14091a54d),
    'v3_post_swap_delta':(0x141e80625,0x141e8073b),
    'unwind_cookie_handler':(0x14292c66c,0x14292c689),
    'unwind_cookie_helper':(0x14292c68c,0x14292c6ec),
    'cookie_check':(0x14292b7d0,0x14292b7ee),
    'registry_load_reset':(0x140bb75e4,0x140bb76e1),
    'registry_load_exception_reset':(0x140bb8863,0x140bb89ac),
    'empty_dict_constructor':(0x1428fec80,0x1428fed0f),
}


def report(exe):
    tables={k:TABLES[k] for k in ('wallet_sweep','commercial_features')}
    # Public method/event names only; private deployment material is not exported.
    from tools.audit_native import constants
    data=exe.read_bytes()
    selected={m:[r['index'] for r in constants(data,m) if isinstance(r['value'],str)
                 and r['value'] in ('wait','status','_close_trader','failed','append','_emit',
                    'wallet_sweep_error','wallet_sweep_complete','safe_error','_manual_lock',
                    'release','_finish_wallet_sweep_ui','append_log')] for m in tables}
    r=inspect(exe,tables=tables,selected=selected,ranges=RANGES)
    r['scope']='bounded instruction windows, some begin inside a basic block; hashes are evidence anchors, not full CFG'
    r['observations']={
        'close_after_normal_result':'0x1420982c2 calls _close_trader; null result branches to error path at 0x142098610',
        'close_while_unwinding':'0x142098581 calls _close_trader; on null takes current tstate error into RBX instead of normal re-raise helper at 0x1420985b5',
        'target_exception':'0x14209c00d matches Exception; failed append and TARGET FAILED continuing precede next-item branch at 0x14209c412; secondary handler errors can escape',
        'outer_worker':'emits wallet_sweep_complete from run result or wallet_sweep_error using safe_error; _manual_lock.release appears on cleanup paths',
        'gui_error':'calls _finish_wallet_sweep_ui then logs WALLET SWEEP ERROR',
        'v3_delta_threshold':'0x141e806d9 subtracts pre-swap balance from post-swap balance; 0x141e80700 loads shared zero at 0x1429a02b0; 0x141e8070e calls <= helper, true enters failure branch',
        'unwind_handler':'0x14292c66c calls cookie helper 0x14292c68c and returns 1; helper reconstructs stack cookie then tailcalls checker 0x14292b7d0; this is not a Python rollback handler',
        'registry_load_error':'Exception match 0x140bb887b; empty-dict helper at 0x140bb889b; _records replacement 0x140bb8931; exception type __name__ stored as load_error at 0x140bb89a7. This is _load, not proof of rollback after _save.',
        'limit':'Error notification is not proof that no transaction was sent or confirmed; no durable journal inferred'}
    return r


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.write_text(json.dumps(report(a.exe),indent=2)+'\n')
