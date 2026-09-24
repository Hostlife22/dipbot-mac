"""Export allowlisted findings, never full strings or decompiled source."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from tools.audit_native import SHA256

TERMS = ['secure_vault.dat', 'ui_state.json', 'CATALOG_TOKEN', 'runtime-settings',
         '_save_ui_state', '_autopair_timer', '_close_trader', 'CryptProtectData',
         'CryptUnprotectData', 'pending_buy', 'pending_sell']


def summarize(root, exe):
    if hashlib.sha256(exe.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Unexpected original release')
    report = {'exe_sha256':SHA256, 'original_executed':False,
              'tools':json.loads((root/'tool-versions.json').read_text())}
    floss = json.loads((root/'floss-static.json').read_text())
    strings = floss['strings']['static_strings']
    report['floss_static'] = {
        'count':len(strings),
        'allowlisted_matches':{term:[hex(row['offset']) for row in strings if term in row['string']]
                               for term in TERMS}}
    for stem in ('capa', 'capa-file', 'capa-file-api', 'floss-functions'):
        path = root/(stem+'.json')
        exit_path = root/(stem+'-exit.txt')
        result = {'exit_code':int(exit_path.read_text()) if exit_path.exists() else None}
        if path.exists() and path.stat().st_size:
            payload = json.loads(path.read_text())
            if stem.startswith('capa'):
                result['scope'] = payload.get('scope', 'full_static')
                result['rules'] = {name:{'namespace':rule['meta'].get('namespace'),
                                        'matches':[match[0] for match in rule.get('matches',[])]}
                                   for name,rule in payload.get('rules',{}).items()}
            else:
                result['string_counts'] = {name:len(rows) for name,rows in payload['strings'].items()}
        report[stem] = result
    log = (root/'ghidra-analysis.log').read_text()
    initial = (root/'ghidra-analysis-initial.log').read_text()
    report['ghidra'] = {'initial_analysis_timeout':bool(re.search(r'(?i)(timed out|analysis timeout)', initial)),
                       'targeted_script_errors':bool(re.search(r'ERROR|AUDIT_FAILED', log)),
                       'exports':re.findall(r'AUDIT_OK ([^\r\n]+)', log),
                       'failures':re.findall(r'AUDIT_FAILED ([^\r\n]+)',log),
                       'decompiled_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in sorted((root/'decompiled').glob('*.c'))}}
    report['instruction_comparison'] = json.loads((root/'instruction-comparison.json').read_text())
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize(args.root,args.exe),ensure_ascii=False,indent=2)+'\n')
