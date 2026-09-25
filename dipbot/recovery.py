"""Read-only comparison of saved holdings against one canonical BSC block."""
from .chain import address


def compare_positions(chain, store):
    block = chain.check(force_network=False)
    header = chain.w3.eth.get_block(block)
    rows = []
    for key, position in list(store.data.get('positions', {}).items()):
        owner = address(key.split(':', 1)[0])
        pool = position['pool']
        token = address(pool['token'])
        actual = chain.balance_at(token, owner, block)
        saved = int(position['amount'])
        rows.append({'owner':owner, 'token':token, 'pool':pool['address'],
                     'decimals':pool['token_decimals'], 'saved_raw':saved,
                     'actual_raw':actual, 'matches':actual == saved})
    if chain.w3.eth.get_block(block)['hash'] != header['hash']:
        raise ValueError('Блок изменился во время сверки; повторите чтение')
    return {'block':block, 'rows':rows}
