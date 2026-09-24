"""Hash-locked bounded evidence for final balances, REMOVE retry and save failure."""
import argparse
import json
from pathlib import Path
from tools.recovery_native import inspect

TABLES={'wallet_sweep':0x142a6fd50,'commercial_features':0x1429db320,'dynamic_pairs':0x1429ed8b0,'gui':0x142a0cf60,'secure_store':0x142a5aa10}
SELECTED={'wallet_sweep':[78,125,183,185,210,211],
          'commercial_features':[99,101,173], 'dynamic_pairs':[], 'gui':[126,634,635,840], 'secure_store':[151,156,158]}
RANGES={'final_balance_classification':(0x14209e420,0x14209e4c5),
        'remaining_dedup_append':(0x14209e76f,0x14209e881),
        'unknown_balance_failure':(0x14209e881,0x14209ea7a),
        'remove_schedule_again':(0x140908d9a,0x140908ea9),
        'registry_remove_save_failure':(0x140bc126e,0x140bc15e5),
        'trader_constructor':(0x140f0c002,0x140f0c114),
        'bot_constructor':(0x140f54faa,0x140f55106),
        'secure_missing_file':(0x141dd072c,0x141dd0833),
        'secure_payload_checks':(0x141dd0949,0x141dd0b64)}


def report(exe):
    r=inspect(exe,tables=TABLES,selected=SELECTED,ranges=RANGES)
    r['scope']='bounded instruction windows; not complete functions, exception graph or runtime comparison'
    r['observations']={
        'unknown_final_balance':['0x14209e494: comparison against None; None bypasses numeric comparison',
                                 '0x14209e7c6: existing remaining label skips duplicate append',
                                 '0x14209e881: None-specific branch adds failure message after remaining'],
        'missing_settings':'Absent file branch constructs SecureStoreError at 0x141dd07e2; outer fallback not resolved',
        'remove_retry':'0x140908e60 calls _schedule_autopair with current token.text()',
        'save_failure':'0x140bc1279 branches on non-null _save result; null goes into exception cleanup at 0x140bc127f; full rollback not proven',
        'representation_difference':'Windows remaining is a deduplicated list of labels; Mac separates measured remaining amounts from unknown tokens'}
    return r


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exe',type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.write_text(json.dumps(report(a.exe),ensure_ascii=False,indent=2)+'\n')
