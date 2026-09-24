"""Convert an explicitly supplied public Windows UI JSON to Mac preferences.

Does not read default paths, decrypt DPAPI, access Keychain or update running app
state. Output is created exclusively; existing files are never overwritten.
"""
import argparse
import json
from pathlib import Path
from dipbot.preferences import from_windows_ui


def convert(source, output):
    payload=json.loads(source.read_text(encoding='utf-8'))
    if (not isinstance(payload,dict) or 'format' in payload
            or not any(k in payload for k in ('trade','pair_amounts','trade_router','pair'))):
        raise ValueError('Нужен публичный Windows UI JSON, не защищённый vault')
    value=from_windows_ui(payload)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    convert(args.input,args.output)
    print('Public preferences converted; application state was not changed')
