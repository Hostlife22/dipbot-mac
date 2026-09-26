"""Exercise a pending replacement on a new isolated Anvil; no external RPC or wallet."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from decimal import Decimal as D
from eth_account import Account
from web3 import Web3
from dipbot.market.chain import Chain
from dipbot.persistence.storage import Store
from dipbot.execution.trader import LiveTrader
from dipbot.execution.reconciliation import reconcile_receipts
from dipbot.execution.cancellation import cancellation_plan, cancel_pending


def run(binary, output):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([str(binary),'--host','127.0.0.1','--port',str(port),
        '--chain-id','56','--base-fee','0','--silent'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    report={'environment':'isolated local Anvil','mainnet_transactions_sent':0,'passed':False}
    try:
        chain=Chain(f'http://127.0.0.1:{port}',request_timeout=2)
        deadline=time.monotonic()+10
        while True:
            if process.poll() is not None:raise RuntimeError('Anvil exited')
            try:chain.check();break
            except Exception:
                if time.monotonic()>deadline:raise
                time.sleep(.1)
        def control(method,params):
            response=chain.w3.provider.make_request(method,params)
            if 'error' in response:raise RuntimeError('Local control failed')
            return response.get('result')
        account=Account.create();recipient=Account.create().address
        control('anvil_setBalance',[account.address,hex(10**18)])
        control('evm_setAutomine',[False])
        original_tx={'chainId':56,'nonce':0,'to':recipient,'value':10**12,'gas':21000,'gasPrice':10**9}
        original=account.sign_transaction(original_tx)
        original_hash=Web3.to_hex(Web3.keccak(original.raw_transaction))
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'state.json')
            store.data['operation']={'wallet':account.address,'description':'LOCAL pending cancellation',
                'transactions':[{'hash':original_hash,'nonce':0,'status':'pending','stage':'prepared',
                                 'broadcast_route':'primary','request':original_tx}]}
            store.save()
            chain.w3.eth.send_raw_transaction(original.raw_transaction)
            # Local node starts without historical receipts. This test uses its own receipts.
            chain.check_receipt_access=lambda:None
            trader=LiveTrader(chain,account.key,store,D(1),lambda _:None)
            plan=cancellation_plan(store.data['operation'],D(1))
            miner_stop=threading.Event()
            def mine_replacement():
                deadline=time.monotonic()+10
                while not miner_stop.is_set() and time.monotonic()<deadline:
                    rows=store.data['operation']['transactions']
                    if len(rows)>1 and control('eth_getTransactionByHash',[rows[-1]['hash']]) is not None:
                        control('evm_mine',[])
                        return
                    miner_stop.wait(.05)
            miner=threading.Thread(target=mine_replacement,daemon=True)
            miner.start()
            try:
                cancel_pending(trader,expected_hash=original_hash,expected_gas_price=plan['gas_price'])
            finally:
                miner_stop.set();miner.join(timeout=2)
            reconcile_receipts(chain,store,account.address)
            rows=store.data['operation']['transactions']
            report.update(statuses=[r['status'] for r in rows],
                recipient_balance=chain.w3.eth.get_balance(recipient),
                wallet_nonce=chain.w3.eth.get_transaction_count(account.address),
                gas_wei=sum(r['gas_fee_wei'] for r in rows),latch_preserved='operation' in store.data)
            report['passed']=(report['statuses']==['superseded','confirmed'] and
                report['recipient_balance']==0 and report['wallet_nonce']==1 and report['latch_preserved'])
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        output.write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--anvil',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    report=run(args.anvil,args.output)
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
