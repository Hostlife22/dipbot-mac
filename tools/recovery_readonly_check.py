"""Public BSC data and isolated synthetic records; no wallet/key access."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from web3 import Web3
from dipbot.market.chain import Chain, USDT, address
from dipbot.persistence.storage import Store, Vault
from dipbot.application.worker import Worker
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.recovery import compare_positions


def run(output):
    chain = Chain('https://bsc-dataseed.binance.org')
    calls = Counter()
    original = chain.w3.provider.make_request
    def read(method, params):
        if method not in {'eth_chainId','eth_getBlockByNumber','eth_getTransactionReceipt','eth_call','eth_getCode'}:
            raise RuntimeError('Non-read method blocked')
        calls[method] += 1
        return original(method, params)
    chain.w3.provider.make_request = read
    block = chain.w3.eth.get_block(chain.check()-3)
    receipt = None
    for tx in block['transactions'][:10]:
        candidate = chain.w3.eth.get_transaction_receipt(tx)
        if candidate['status'] == 1:
            receipt = candidate
            break
    assert receipt is not None
    chain.canonical_receipt(receipt)
    pool = chain.verify_pool('0x16b9a82891338f9bA80E2D6970FddA79D1eb0daE', address(USDT))
    owner = pool.address  # Public pool address, not the user's wallet.
    balance = chain.balance_at(pool.token, owner, receipt['blockNumber'])
    chain.canonical_receipt(receipt)
    report = {'utc':datetime.now(timezone.utc).isoformat(),'transactions_sent':0,
              'receipt_hash':Web3.to_hex(receipt['transactionHash']),
              'receipt_block':receipt['blockNumber'],'receipt_block_hash':Web3.to_hex(receipt['blockHash']),
              'balance_owner':owner,'balance_token':pool.token,'balance_at_receipt_raw':balance,
              'fixture':'Synthetic saved position at a public pool address; not user holdings'}
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory)/'state.json')
        store.data['positions'] = {owner.lower()+':'+pool.address.lower():
            {'pool':asdict(pool),'amount':2**255,'entry':'1'}}
        store.data['operation'] = {'wallet':owner,'description':'read-only fixture',
            'transactions':[{'hash':report['receipt_hash'],'status':'pending'}]}
        store.save()
        worker = Worker(store); worker.chain=chain
        def forbidden(*a, **kw):
            raise AssertionError('Keychain access forbidden')
        with patch.object(Vault,'get',forbidden):
            worker.command('reconcile',{})
            assert store.data['operation']['transactions'][0]['status']=='confirmed'
            assert store.data['operation']
            report['confirmed_receipt_keeps_lock']=True
            result=compare_positions(chain,store)
            assert not result['rows'][0]['matches']
            assert store.data['positions'][next(iter(store.data['positions']))]['amount']==2**255
            report['comparison']=result
            store.data['operation']['transactions']=[{'hash':'0x'+'00'*32,'status':'pending'}]
            try:worker.command('reconcile',{})
            except UncertainTransaction:report['unknown_hash_keeps_lock']=bool(store.data['operation'])
            else:raise AssertionError('Unknown hash accepted')
            def unavailable(*a, **kw):raise TimeoutError('Injected RPC outage')
            with patch.object(chain,'check',unavailable):
                try:worker.command('reconcile',{})
                except TimeoutError:report['injected_rpc_failure_keeps_lock']=bool(store.data['operation'])
                else:raise AssertionError('RPC failure hidden')
    report['rpc_methods']=dict(calls)
    report['passed']=True
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
