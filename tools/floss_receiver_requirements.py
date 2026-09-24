"""Join observed FLOSS sites to static access paths; never inject invented receivers."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def report(directory, inventory=None):
    files = ['RECEIVER_FLOW_EVIDENCE.json', 'EXCEPTION_FOLLOWUP_EVIDENCE.json',
             'ARGUMENT_AUDIT_EVIDENCE.json', 'FLOSS_NAMES_MODEL_EVIDENCE.json']
    loaded = {f: json.loads((directory/f).read_text()) for f in files}
    index = {}
    for f in files[:-1]:
        doc = loaded[f]
        for caller, body in doc.get('ranges', doc.get('closures', {})).items():
            for row in body.get('calls', []):
                if row.get('receiver'):
                    entry = {'caller': caller, 'receiver_path': row['receiver']}
                    if entry not in index.setdefault(row['site'], []):
                        index[row['site']].append(entry)
    if inventory is not None:
        for caller, body in json.loads(inventory.read_text())['functions'].items():
            for row in body['helper_calls']:
                if row.get('receiver'):
                    entry = {'caller': caller, 'receiver_path': row['receiver']}
                    if entry not in index.setdefault(row['site'], []):
                        index[row['site']].append(entry)
    counts = Counter()
    output = {}
    for helper, data in loaded[files[-1]]['functions'].items():
        rows = []
        for sample in data['samples']:
            candidates = index.get(sample['site'], [])
            paths = sorted({r['receiver_path'] for r in candidates})
            classification = ('static_path_available' if len(paths) == 1 else
                              'conflicting_static_paths' if paths else 'no_static_receiver_path')
            counts[classification] += 1
            rows.append({**sample, 'static_candidates': candidates, 'classification': classification})
        output[helper] = rows
    hashes = {f: hashlib.sha256((directory/f).read_bytes()).hexdigest() for f in files}
    if inventory is not None:
        hashes[inventory.name] = hashlib.sha256(inventory.read_bytes()).hexdigest()
    return {'inputs_sha256': hashes,
            'original_executed': False, 'new_emulation_run': False, 'objects_injected_by_this_tool': False,
            'counts': dict(counts), 'functions': output,
            'limitations': ['Static paths are requirements for initialization, not concrete runtime values',
                            'Known path does not repair the absent receiver header',
                            'Method dispatch also requires initialized types, descriptors, module globals and tstate']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--inventory', type=Path)
    a = p.parse_args()
    a.output.write_text(json.dumps(report(a.directory, a.inventory), indent=2)+'\n')
