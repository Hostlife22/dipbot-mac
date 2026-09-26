"""Read-only comparison of saved holdings against one canonical BSC block."""
from dipbot.market.chain import address


def compare_positions(chain, store):
    block = chain.check(force_network=False)
    header = chain.w3.eth.get_block(block)
    groups = {}
    for key, position in list(store.data.get('positions', {}).items()):
        owner = address(key.split(':', 1)[0])
        pool = position['pool']
        token = address(pool['token'])
        saved = position['amount']
        decimals = pool['token_decimals']
        if type(saved) is not int or not 0 <= saved < 2**256 or type(decimals) is not int or not 0 <= decimals <= 36:
            raise ValueError('Некорректное количество или decimals сохранённой позиции')
        group = groups.setdefault((owner, token), {'decimals': decimals, 'saved': 0, 'pools': []})
        if group['decimals'] != decimals:
            raise ValueError('Разные decimals для одного токена в сохранённых позициях')
        group['saved'] += saved
        group['pools'].append(pool['address'])
    rows = []
    for (owner, token), group in groups.items():
        actual = chain.balance_at(token, owner, block)
        if type(actual) is not int or not 0 <= actual < 2**256:
            raise ValueError('RPC вернул некорректный баланс')
        rows.append({'owner': owner, 'token': token, 'pool': ', '.join(group['pools']),
                     'decimals': group['decimals'], 'saved_raw': group['saved'],
                     'actual_raw': actual, 'matches': actual == group['saved'],
                     'position_count': len(group['pools'])})
    if chain.w3.eth.get_block(block)['hash'] != header['hash']:
        raise ValueError('Блок изменился во время сверки; повторите чтение')
    return {'block':block, 'rows':rows}
