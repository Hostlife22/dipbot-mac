"""capa 9.4 file-only API: no claims about function-level capabilities."""
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_native import SHA256


def report(exe, rules):
    import capa.rules
    import capa.rules.cache
    from capa.features.extractors.pefile import PefileFeatureExtractor
    from capa.capabilities.common import find_file_capabilities
    if hashlib.sha256(exe.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Unexpected release')
    ruleset = capa.rules.get_rules([rules])
    result = find_file_capabilities(ruleset, PefileFeatureExtractor(exe), {})
    return {'scope':'file_only', 'exe_sha256':SHA256, 'feature_count':result.feature_count,
            'rules':{name:{'meta':{'namespace':ruleset.rules[name].meta.get('namespace')},'matches':[]}
                     for name in sorted(result.matches)}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exe',type=Path)
    parser.add_argument('--rules',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(report(args.exe,args.rules),indent=2)+'\n')
