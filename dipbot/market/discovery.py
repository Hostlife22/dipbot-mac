"""Read-only address resolver and block-pinned Multicall discovery."""
from dataclasses import dataclass
from dipbot.market.autopair import Candidate, choose, ordered
from dipbot.market.chain import (address, Pool, POOL_ABI, FACTORY_ABI, V2_FACTORY, V3_FACTORY,
                    ZERO, FEES)

MULTICALL = '0xcA11bde05977b3631167028862bE2a173976CA11'
MULTICALL_ABI = [{'name':'aggregate3','type':'function','stateMutability':'payable',
 'inputs':[{'name':'calls','type':'tuple[]','components':[
 {'name':'target','type':'address'},{'name':'allowFailure','type':'bool'},{'name':'callData','type':'bytes'}]}],
 'outputs':[{'name':'returnData','type':'tuple[]','components':[
 {'name':'success','type':'bool'},{'name':'returnData','type':'bytes'}]}]}]


@dataclass(frozen=True)
class Resolution:
    state: str
    candidates: tuple = ()
    selected: object = None
    target: str = ''
    input_kind: str = 'TOKEN'


def batch(chain, requests, block, *, allow_empty=False):
    if not requests:
        return []
    calls = [(address(addr), True, chain.contract(addr, abi).get_function_by_name(name)(*args)._encode_transaction_data())
             for addr, abi, name, args in requests]
    raw = chain.contract(MULTICALL, MULTICALL_ABI).functions.aggregate3(calls).call(block_identifier=block)
    if len(raw) != len(requests):
        raise ValueError('Multicall вернул неполный снимок')
    result = []
    for (success, data), (_, abi, name, _) in zip(raw, requests):
        if not success or (allow_empty and not data):
            result.append(None)
            continue
        outputs = next(fn['outputs'] for fn in abi if fn.get('name') == name)
        types = [out['type'] for out in outputs]
        # Decode errors are not a trustworthy "zero balance" or "no pool".
        values = chain.w3.codec.decode(types, data)
        result.append(values[0] if len(values) == 1 else values)
    return result


def request(addr, abi, name, *args):
    return addr, abi, name, args


def discover(chain, target, catalogs, block=None):
    block = chain.check() if block is None else block
    plans, queries = [], []
    for router, catalog in catalogs.items():
        for name, quote in catalog.items():
            quote = address(quote)
            if quote == target:
                continue
            for fee in ((0,) if router == 'V2' else FEES):
                plans.append((router, name, quote, fee))
                queries.append(request(V2_FACTORY, FACTORY_ABI, 'getPair', target, quote) if router == 'V2'
                               else request(V3_FACTORY, FACTORY_ABI, 'getPool', target, quote, fee))
    found = batch(chain, queries, block)
    checks, pending, seen = [], [], set()
    for plan, raw in zip(plans, found):
        if raw is None or raw.lower() == ZERO:
            continue
        pool_address = address(raw)
        router, name, quote, fee = plan
        identity = (router, name, pool_address)
        if identity in seen: continue
        seen.add(identity)
        pending.append((plan, pool_address))
        checks.extend(request(pool_address, POOL_ABI, method) for method in
                      ('factory','token0','token1','getReserves' if router=='V2' else 'liquidity'))
    values = batch(chain, checks, block)
    candidates = []
    for i, ((router, name, quote, fee), pool_address) in enumerate(pending):
        factory, token0, token1, raw = values[4*i:4*i+4]
        if any(v is None for v in (factory, token0, token1, raw)):
            continue
        expected = V2_FACTORY if router == 'V2' else V3_FACTORY
        if address(factory) != address(expected) or {address(token0), address(token1)} != {target, quote}:
            raise ValueError('Multicall: factory/стороны пула не совпадают')
        score = raw[0]*raw[1] if router == 'V2' else raw
        pool = Pool(pool_address, router, target, quote, chain.decimals(target), chain.decimals(quote),
                    address(token0)==target, fee)
        candidates.append(Candidate(pool,name,score>0,score))
    return ordered(candidates)


def resolve(chain, raw, catalogs):
    entered = address(raw)
    block = chain.check()
    if not chain.w3.eth.get_code(entered, block_identifier=block):
        return Resolution('INVALID_CONTRACT', target=entered)
    factory, t0, t1 = batch(chain, [request(entered,POOL_ABI,m) for m in ('factory','token0','token1')], block, allow_empty=True)
    if factory is not None and t0 is not None and t1 is not None:
        factory = address(factory)
        router = 'V2' if factory==address(V2_FACTORY) else 'V3' if factory==address(V3_FACTORY) else None
        if router:
            names = {address(token):name for name,token in catalogs.get(router,{}).items()}
            t0,t1 = address(t0),address(t1)
            if (t0 in names) == (t1 in names):
                return Resolution('UNSUPPORTED_POOL',target=entered,input_kind=router+'_POOL')
            target, quote = (t1,t0) if t0 in names else (t0,t1)
            try:
                pool = chain.verify_pool(entered,target,require_liquidity=False)
            except ValueError:
                return Resolution('UNSUPPORTED_POOL',target=target,input_kind=router+'_POOL')
            raw_score = batch(chain,[request(entered,POOL_ABI,'getReserves' if router=='V2' else 'liquidity')],block)[0]
            if raw_score is None: raise ValueError('Не удалось проверить ликвидность пула')
            score = raw_score[0]*raw_score[1] if router=='V2' else raw_score
            candidate = Candidate(pool,names[quote],score>0,score)
            return Resolution('RESOLVED' if score>0 else 'PENDING',(candidate,),candidate,target,router+'_POOL')
    if any(entered == address(token) for catalog in catalogs.values() for token in catalog.values()):
        return Resolution('CATALOG_TOKEN',target=entered,input_kind='PROFILE_TOKEN')
    candidates = discover(chain,entered,catalogs,block)
    state, selected = choose(candidates)
    return Resolution(state,tuple(candidates),selected,entered)
