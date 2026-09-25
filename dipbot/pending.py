"""Read-only evidence about a missing receipt; never a dropped/replace permission."""
import time
from web3 import Web3
from web3.exceptions import TransactionNotFound

LABELS = {
    'pending_visible': 'Транзакция видна узлу, receipt пока отсутствует',
    'mined_receipt_missing': 'Узел сообщает блок транзакции, но receipt недоступен',
    'replacement_found': 'Найдена каноническая транзакция с тем же nonce; требуется сверка балансов',
    'nonce_consumed': 'Nonce уже использован: нужна сверка исходной или заменяющей транзакции',
    'pending_nonce_advanced': 'Есть pending nonce выше сохранённого; hash не найден этим узлом',
    'not_visible': 'Узел не видит hash; потеря транзакции не доказана',
    'unavailable': 'Дополнительная проверка недоступна; результат неизвестен',
    'legacy_unknown': 'В старой записи нет nonce для дополнительной проверки',
}


def inspect_missing(chain, owner, record):
    result = {'checked_at':int(time.time()), 'state':'legacy_unknown'}
    nonce = record.get('nonce')
    if type(nonce) is not int or nonce < 0:
        return result
    try:
        try:
            tx = chain.w3.eth.get_transaction(record['hash'])
        except TransactionNotFound:
            tx = None
        if tx is not None:
            if (Web3.to_hex(tx['hash']).lower() != record['hash'].lower()
                    or tx['from'].lower() != owner.lower() or tx['nonce'] != nonce):
                raise ValueError('Несогласованные данные транзакции')
            result['state'] = 'pending_visible' if tx.get('blockNumber') is None else 'mined_receipt_missing'
        else:
            latest = chain.w3.eth.get_transaction_count(owner, 'latest')
            pending = chain.w3.eth.get_transaction_count(owner, 'pending')
            if any(type(n) is not int or n < 0 for n in (latest,pending)) or pending < latest:
                raise ValueError('Несогласованные nonce узла')
            result.update(latest_nonce=latest, pending_nonce=pending)
            result['state'] = ('nonce_consumed' if latest > nonce else
                               'pending_nonce_advanced' if pending > nonce else 'not_visible')
            if latest > nonce and callable(getattr(chain.w3.eth, 'get_block', None)):
                try:
                    evidence = find_nonce_replacement(chain, owner, record)
                    result['replacement_search'] = evidence
                    if evidence.get('hash') and evidence['hash'].lower() != record['hash'].lower():
                        result['state'] = 'replacement_found'
                except Exception as exc:
                    result['replacement_search'] = {'complete':False, 'error_type':type(exc).__name__}
    except Exception as exc:
        result = {'checked_at':int(time.time()),'state':'unavailable','error_type':type(exc).__name__}
    return result


def find_nonce_replacement(chain, owner, record, *, max_blocks=32, timeout=8):
    """Bounded read-only evidence. A found replacement never clears the latch."""
    from .trader import LiveTrader
    started = time.monotonic()
    eth = chain.w3.eth
    head = eth.get_block('latest')
    number = head['number']
    if type(number) is not int or number < 0:
        raise ValueError('Invalid search head')
    floor = max(0, number-max_blocks+1)
    prepared = record.get('prepared_block')
    if type(prepared) is int and 0 <= prepared <= number:
        floor = max(floor, prepared)
    scanned = 0
    for height in range(number, floor-1, -1):
        if time.monotonic()-started >= timeout:
            return {'scanned_blocks':scanned, 'complete':False}
        block = eth.get_block(height, full_transactions=True)
        if block['number'] != height:
            raise ValueError('Invalid search block')
        scanned += 1
        for tx in block['transactions']:
            if tx.get('from', '').lower() != owner.lower() or tx.get('nonce') != record['nonce']:
                continue
            tx_hash = Web3.to_hex(tx['hash'])
            receipt = eth.get_transaction_receipt(tx_hash)
            LiveTrader.validate_receipt(receipt, tx_hash)
            if receipt['blockNumber'] != height or receipt['blockHash'] != block['hash']:
                raise ValueError('Replacement receipt disagrees with block')
            chain.canonical_receipt(receipt)
            return {'hash':tx_hash,'block':height,'block_hash':Web3.to_hex(block['hash']),
                    'receipt_status':receipt['status'],'scanned_blocks':scanned,'complete':True}
    return {'scanned_blocks':scanned,'complete':False}
