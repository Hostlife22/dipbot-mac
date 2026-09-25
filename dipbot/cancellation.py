"""Explicit same-nonce cancellation. Never retries the trade or unlocks holdings."""
import time
from decimal import Decimal as D
from web3 import Web3
from web3.exceptions import TransactionNotFound
from .accounting import record_gas, marked_value
from .chain import WBNB


def cancellation_plan(operation, gas_gwei):
    if not operation:
        raise ValueError('Нет незавершённой операции')
    if not gas_gwei.is_finite() or not 0 < gas_gwei <= 1000:
        raise ValueError('Некорректный GAS GWEI')
    records = operation.get('transactions', [])
    pending = [r for r in records if r.get('status') == 'pending' and not r.get('replaces')]
    if len(pending) != 1:
        raise ValueError('Отмена поддерживает одну неопределённую исходную транзакцию')
    target = pending[0]
    request = target.get('request', {})
    nonce, old_fee = target.get('nonce'), request.get('gasPrice')
    if (type(nonce) is not int or nonce < 0 or type(old_fee) is not int or old_fee <= 0
            or request.get('chainId') != 56 or request.get('nonce') != nonce):
        raise ValueError('В записи недостаточно проверенных данных для отмены')
    attempts = [r for r in records if r.get('replaces') == target['hash']]
    if len(attempts) >= 3:
        raise ValueError('Достигнут предел: 3 попытки отмены; нужна ручная сверка')
    for attempt in attempts:
        saved = attempt.get('request', {})
        fee = saved.get('gasPrice')
        if (attempt.get('status') != 'pending' or attempt.get('nonce') != nonce
                or saved.get('chainId') != 56 or saved.get('nonce') != nonce
                or type(fee) is not int or fee <= 0
                or attempt.get('broadcast_route','primary') != target.get('broadcast_route','primary')):
            raise ValueError('Предыдущая отмена требует сверки, новый повтор запрещён')
        prepared = attempt.get('prepared_at')
        if type(prepared) is not int or not 30 <= time.time()-prepared:
            raise ValueError('Отмена уже записана: до повторного просмотра подождите 30 секунд')
        old_fee = max(old_fee, fee)
    gas_price = max(int(gas_gwei*10**9), (old_fee*125+99)//100)
    maximum_fee = gas_price*21000
    if maximum_fee > 5*10**15:
        raise ValueError('Комиссия отмены превышает 0.005 BNB')
    return {'original_hash':target['hash'], 'nonce':nonce, 'gas_price':gas_price,
            'attempt':len(attempts)+1, 'maximum_fee_wei':maximum_fee, 'broadcast_route':target.get('broadcast_route','primary')}


def cancel_pending(trader, *, expected_hash, expected_gas_price):
    from .trader import UncertainTransaction
    operation = trader.store.data.get('operation')
    plan = cancellation_plan(operation, D(trader.gas_price)/10**9)
    if operation['wallet'].lower() != trader.owner.lower():
        raise ValueError('Кошелёк не совпадает с незавершённой операцией')
    if plan['original_hash'] != expected_hash or plan['gas_price'] != expected_gas_price:
        raise ValueError('План отмены изменился; повторите просмотр и подтверждение')
    chain = trader.chain
    chain.check()
    eth = chain.w3.eth
    broadcaster = chain
    if plan['broadcast_route'] == 'custom':
        broadcaster = getattr(trader, 'broadcast_chain', None)
        if broadcaster is None:
            raise ValueError('Для отмены нужен отдельный RPC исходной отправки')
        broadcaster.check()
    elif plan['broadcast_route'] != 'primary':
        raise ValueError('Неизвестный маршрут исходной отправки')
    # Any sibling can win while a previous RPC acknowledgement is missing.
    for candidate in operation['transactions']:
        if candidate['hash'] != plan['original_hash'] and candidate.get('replaces') != plan['original_hash']:
            continue
        try:
            eth.get_transaction_receipt(candidate['hash'])
        except TransactionNotFound:
            pass
        else:
            raise ValueError('Receipt исходной сделки или отмены уже найден; сначала выполните сверку')
    nonce = plan['nonce']
    latest, pending = eth.get_transaction_count(trader.owner,'latest'), eth.get_transaction_count(trader.owner,'pending')
    if type(latest) is not int or type(pending) is not int or latest != nonce or pending not in (nonce,nonce+1):
        raise ValueError('Nonce изменился или есть другие pending; сначала выполните сверку')
    if eth.get_code(trader.owner):
        raise ValueError('Отмена поддерживается только для EOA без кода/делегации')
    if eth.get_balance(trader.owner) < plan['maximum_fee_wei']:
        raise ValueError('Недостаточно BNB для комиссии отмены')
    tx = {'chainId':56,'nonce':nonce,'to':trader.owner,'value':0,'data':'0x',
          'gas':21000,'gasPrice':plan['gas_price']}
    signed = trader.account.sign_transaction(tx)
    tx_hash = Web3.to_hex(Web3.keccak(signed.raw_transaction))
    record = {'hash':tx_hash,'nonce':nonce,'label':'CANCEL SAME NONCE','replaces':plan['original_hash'],
              'status':'pending','stage':'prepared','prepared_at':int(time.time()),
              'broadcast_route':plan['broadcast_route'],'request':tx}
    operation['transactions'].append(record)
    trader.store.save()  # Durable cancellation hash before broadcast.
    try:
        remote = broadcaster.w3.eth.send_raw_transaction(signed.raw_transaction)
        if Web3.to_hex(remote) != tx_hash:
            raise ValueError('Wrong cancellation hash')
        record.update(stage='submitted', submitted_at=int(time.time()))
        trader.store.save()
        receipt = eth.wait_for_transaction_receipt(tx_hash, timeout=120,poll_latency=.2)
    except Exception:
        raise UncertainTransaction('Исход отмены неизвестен; торговля заблокирована. Проверьте все receipts перед новой попыткой') from None
    trader.validate_receipt(receipt,tx_hash)
    trader.check_canonical(receipt)
    record.update(status='confirmed' if receipt['status']==1 else 'reverted',
                  stage='receipt_validated',block=receipt['blockNumber'],
                  block_hash=Web3.to_hex(receipt['blockHash']),receipt_at=int(time.time()))
    if 'gasUsed' in receipt and 'effectiveGasPrice' in receipt:
        record['gas_fee_wei']=receipt['gasUsed']*receipt['effectiveGasPrice']
        rates=getattr(trader,'rates',None)
        rate=rates.snapshot(WBNB) if rates is not None else None
        record['gas_usd_rate']=rate
        record['gas_usd']=marked_value(D(record['gas_fee_wei'])/10**18,rate)
        record_gas(trader.store,trader.owner,record)
    trader.store.save()
    return 'Транзакция отмены включена в блок. Проверьте оба receipts и балансы; торговля остаётся заблокирована'
