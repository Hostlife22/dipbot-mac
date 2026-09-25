"""Read-only evidence about a missing receipt; never a dropped/replace permission."""
import time
from web3 import Web3
from web3.exceptions import TransactionNotFound

LABELS = {
    'pending_visible': 'Транзакция видна узлу, receipt пока отсутствует',
    'mined_receipt_missing': 'Узел сообщает блок транзакции, но receipt недоступен',
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
    except Exception as exc:
        result = {'checked_at':int(time.time()),'state':'unavailable','error_type':type(exc).__name__}
    return result
