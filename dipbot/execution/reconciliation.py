"""Receipt reconciliation; never rebroadcasts or clears the latch."""
import time
from web3 import Web3
from web3.exceptions import TransactionNotFound
from dipbot.execution.trader import LiveTrader
from dipbot.execution.errors import UncertainTransaction
from dipbot.execution.accounting import record_gas

def reconcile_receipts(chain, store, owner):
    operation = store.data.get("operation")
    if not operation:
        return "Незавершённых операций нет"
    if operation["wallet"].lower() != owner.lower():
        raise ValueError("Для сверки нужен тот же кошелёк, который начал операцию")
    chain.check()
    for record in operation["transactions"]:
        try:
            receipt = chain.w3.eth.get_transaction_receipt(record["hash"])
        except TransactionNotFound:
            if record.get('block_hash') or record.get('status') in ('confirmed','reverted'):
                raise UncertainTransaction('Ранее подтверждённый receipt исчез; нужна ручная сверка reorg') from None
            alternative = None
            for other in operation['transactions']:
                linked = (other.get('replaces') == record['hash'] or record.get('replaces') == other['hash']
                          or (record.get('replaces') is not None and record.get('replaces') == other.get('replaces')))
                if not linked or type(record.get('nonce')) is not int or other.get('nonce') != record['nonce']:
                    continue
                try:
                    proof = chain.w3.eth.get_transaction_receipt(other['hash'])
                except TransactionNotFound:
                    continue
                LiveTrader.validate_receipt(proof, other['hash'])
                if not hasattr(chain,'canonical_receipt'):
                    raise UncertainTransaction('Нельзя проверить канонический receipt замены')
                LiveTrader.retry_read(lambda: chain.canonical_receipt(proof))
                if other.get('block_hash') and other['block_hash'] != Web3.to_hex(proof['blockHash']):
                    raise UncertainTransaction('Блок замены изменился; нужна ручная сверка')
                alternative = other['hash']
                break
            if alternative is not None:
                record.update(status='superseded',stage='same_nonce_resolved',superseded_by=alternative,
                              gas_fee_wei=0,gas_usd='0')
                record.pop('receipt_review',None)
                store.save()
                continue
            from dipbot.execution.pending import inspect_missing, LABELS
            record['receipt_review'] = inspect_missing(chain, owner, record)
            store.save()
            detail = LABELS[record['receipt_review']['state']]
            raise UncertainTransaction(f"Не найден receipt {record['hash']}; {detail}; блокировка сохранена") from None
        LiveTrader.validate_receipt(receipt, record["hash"])
        if hasattr(chain, "canonical_receipt"):
            LiveTrader.retry_read(lambda: chain.canonical_receipt(receipt))
        if record.get('block_hash') and record['block_hash'] != Web3.to_hex(receipt['blockHash']):
            raise UncertainTransaction('Блок ранее подтверждённой транзакции изменился; нужна ручная сверка')
        if 'blockHash' in receipt:
            record['block_hash'] = Web3.to_hex(receipt['blockHash'])
        record["status"] = "confirmed" if receipt["status"] == 1 else "reverted"
        record["block"] = receipt["blockNumber"]
        record["stage"] = "receipt_validated"
        record["receipt_at"] = int(time.time())
        record.pop("receipt_review", None)
        if 'gasUsed' in receipt and 'effectiveGasPrice' in receipt:
            record['gas_fee_wei'] = receipt['gasUsed'] * receipt['effectiveGasPrice']
            record_gas(store, owner, record)
        store.save()
    settled_nonces = [r['nonce'] for r in operation['transactions']
                      if type(r.get('nonce')) is int and r.get('status') in ('confirmed','reverted')]
    if len(settled_nonces) != len(set(settled_nonces)):
        raise UncertainTransaction('Узел вернул несколько включённых транзакций с одним nonce; нужна сверка')
    # Do not clear the latch automatically: balances/position also need review.
    store.save()
    return "Все записанные транзакции завершены. Проверьте балансы; затем снимите блокировку вручную"
