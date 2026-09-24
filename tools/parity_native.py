"""Reproduce selected converter and strategy evidence, without running the EXE.

Run from repo root: python -m tools.parity_native EXE --output FILE
Optional --disassembly DIR requires capstone in the analysis environment only.
"""
import argparse
import json
from pathlib import Path
from tools.audit_native import inspect, RANGES, constants

PARITY_RANGES = RANGES | {
    'converter_bridge_fees': (0x141e840c1, 0x141e840f5),
    'read_snapshot': (0x14088e2e0, 0x14088e6cc),
    'stop_request': (0x1408a07c0, 0x1408a0c79),
    'read_price_wrapper': (0x14088e6d0, 0x14088eebc),
    'preferred_buy_route': (0x141e71fb0, 0x141e72aab),
    'buy_route_candidates': (0x141e72ab0, 0x141e7427e),
    'quote_route': (0x141e74280, 0x141e75b60),
    'roundtrip_loss_bps': (0x141e75b60, 0x141e76003),
    'select_safe_route': (0x141e76660, 0x141e795b2),
    'execute_bnb_to_quote': (0x141e7c400, 0x141e7e6be),
    'execute_quote_to_bnb': (0x141e7e6c0, 0x141e8126f),
}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--disassembly', type=Path)
    args = parser.parse_args()
    report = inspect(args.exe, args.disassembly, ranges=PARITY_RANGES)
    report['profile_metadata'] = [row for row in constants(args.exe.read_bytes(), 'pair_profiles')
        if isinstance(row['value'], list) and 3 <= len(row['value']) <= 4
        and isinstance(row['value'][1], str) and row['value'][1].startswith('0x')]
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print('Selected parity evidence written:', args.output)
