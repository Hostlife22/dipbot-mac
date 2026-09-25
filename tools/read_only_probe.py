"""Explicit read-only BSC integration probe; no wallet, signing or local Store."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit
from dipbot.chain import Chain, WBNB, USDT, address, POOL_ABI
from dipbot.discovery import discover, resolve, MULTICALL, batch, request

ALLOWED = {'eth_chainId','eth_getBlockByNumber','eth_getCode','eth_call','eth_getLogs','eth_getBlockByHash'}


def guard_provider(provider):
    calls = Counter()
    original = provider.make_request
    def read_only(method, params):
        if method not in ALLOWED:
            raise RuntimeError('Non-read RPC method blocked: '+method)
        calls[method] += 1
        return original(method, params)
    provider.make_request = read_only
    return calls


def probe(endpoint):
    chain = Chain(endpoint)
    calls = guard_provider(chain.w3.provider)
    block = chain.check()
    assert chain.w3.eth.get_code(address(MULTICALL))
    catalogs = {'V2':{'WBNB':WBNB},'V3':{'WBNB':WBNB}}
    candidates = discover(chain,address(USDT),catalogs,block)
    # Compare batch and individual reads at the SAME block for every candidate.
    requests = [request(c.pool.address,POOL_ABI,name) for c in candidates
                for name in ('factory','token0','token1')]
    batched = batch(chain,requests,block)
    direct = [chain.call(addr,abi,name,*args,block=block) for addr,abi,name,args in requests]
    assert [address(v) for v in batched] == [address(v) for v in direct]
    checked = []
    for router in ('V2','V3'):
        candidate = next(c for c in candidates if c.ready and c.pool.router == router)
        pool = chain.verify_pool(candidate.pool.address,address(USDT))
        by_pool = resolve(chain,pool.address,catalogs)
        assert by_pool.state == 'RESOLVED' and by_pool.target == address(USDT)
        buy = chain.quote(pool,10**15,True)
        sell = chain.quote(pool,10**18,False)
        assert buy > 0 and sell > 0
        checked.append({'router':router,'pool':pool.address,'fee':pool.fee,
                        'buy_0_001_wbnb_raw':buy,'sell_1_usdt_raw':sell})
    assert resolve(chain,WBNB,catalogs).state == 'CATALOG_TOKEN'
    return {'utc':datetime.now(timezone.utc).isoformat(),'discovery_block':block,
            'endpoint_host':urlsplit(endpoint).hostname,'candidates':len(candidates),'checked':checked,
            'multicall_metadata_reads':len(requests),'multicall_matches_direct':True,
            'rpc_methods':dict(calls),'transactions_sent':0}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint',default='https://bsc-dataseed-public.bnbchain.org')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.write_text(json.dumps(probe(args.endpoint),indent=2)+'\n')
    print('Read-only probe passed:',args.output)
