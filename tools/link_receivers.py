"""Cross-reference receiver paths against factory definitions; no runtime dispatch claim."""
import argparse
import json
from pathlib import Path


def link(flow, factories):
    if flow['exe_sha256']!=factories['exe_sha256']:raise ValueError('Different releases')
    by_body={d['body']:d['qualified_name'] for d in factories['definitions']}
    definitions={d['qualified_name']:d for d in factories['definitions']}
    links=[]
    for caller,body in flow['ranges'].items():
        qualified=by_body.get(body['start'])
        if not qualified:continue
        owner=qualified.split('.')[0]
        for call in body['calls']:
            expression=call.get('expression',call.get('callable',''))
            if not expression.startswith('arg[0].'):continue
            method=expression[7:]
            if not method.isidentifier():continue
            target=definitions.get(owner+'.'+method)
            if target:
                links.append({'caller':qualified,'site':call['site'],'helper':call['helper'],
                              'expression':expression,'candidate_definition':target['qualified_name'],
                              'body':target['body'],'factory':target['factory']})
    return {'exe_sha256':flow['exe_sha256'],
            'scope':'same-class definition candidates assuming arg[0] is self; monkey-patching, subclass overrides and runtime identities unresolved',
            'links':links}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('flow',type=Path);p.add_argument('factories',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.write_text(json.dumps(link(json.loads(a.flow.read_text()),json.loads(a.factories.read_text())),indent=2)+'\n')
